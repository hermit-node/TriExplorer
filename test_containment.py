import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from collections import Counter
from unittest.mock import patch
import archive_matches as a
from services.archive import zip_members
import pycdlib

class ContainmentTests(unittest.TestCase):
    def test_screen_counts_multiplicity(self):
        self.assertTrue(a.size_screen(Counter({1:10}),Counter({1:7,2:3})))
        self.assertFalse(a.size_screen(Counter({1:10}),Counter({1:6,2:4})))

    def test_containment_and_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            folder=root/"different name"
            folder.mkdir()
            data=b"a"*10000
            (folder/"data.bin").write_bytes(data)
            (folder/"extra.bin").write_bytes(b"x"*100000)
            z=root/"sample.zip"
            with zipfile.ZipFile(z,"w",zipfile.ZIP_DEFLATED) as f:
                f.writestr("wrapper/data.bin",data)
            self.assertIsNone(a.compare(str(z),str(folder),zip_members))
            m=a.compare(str(z),str(folder),zip_members,allow_contained=True)
            self.assertTrue(m.verified and m.contained and m.recyclable)
            found,errors=a.find_matches([str(folder)],[(str(z),z.stat().st_size)],{},None,zip_members,allow_contained=True)
            self.assertEqual(errors,[])
            self.assertEqual(len(found),1)
            (folder/"data.bin").write_bytes(b"b"*len(data))
            self.assertIsNone(a.compare(str(z),str(folder),zip_members,allow_contained=True))
            (folder/"data.bin").unlink()
            with patch("duplicates.hash_file") as hash_file:
                self.assertIsNone(a.compare(str(z),str(folder),zip_members,allow_contained=True))
                hash_file.assert_not_called()

    def test_iso(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            folder=root/"files"; folder.mkdir()
            (folder/"hello.txt").write_bytes(b"hello")
            image=root/"image.iso"
            x=pycdlib.PyCdlib()
            x.new(joliet=3)
            x.add_fp(io.BytesIO(b"hello"),5,iso_path="/HELLO.TXT;1",joliet_path="/hello.txt")
            x.write(str(image)); x.close()
            m=a.compare(str(image),str(folder),zip_members)
            self.assertTrue(m and m.verified and m.recyclable)
            (folder/"extra.txt").write_text("extra")
            self.assertIsNone(a.compare(str(image),str(folder),zip_members))
            m=a.compare(str(image),str(folder),zip_members,allow_contained=True)
            self.assertTrue(m and m.verified and m.contained and m.recyclable)
            # Nonempty system area must keep image, even when file data matches.
            with image.open("r+b") as f:
                f.write(b"unique system area")
            m=a.compare(str(image),str(folder),zip_members,allow_contained=True)
            self.assertTrue(m and m.verified and not m.recyclable)

    def test_containment_toggle_and_cleanup(self):
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import QSettings
        from explorer import Explorer
        from test_explorer import wait_for, patch_recycle
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root/"expanded"
            folder.mkdir()
            (folder/"data.txt").write_text("data")
            (folder/"extra.txt").write_text("extra")
            z = root/"compressed.zip"
            with zipfile.ZipFile(z,"w") as f:
                f.writestr("data.txt","data")
            cfg=QSettings(str(root/"settings.ini"), QSettings.IniFormat)
            cfg.setValue("cache/sqlite_path",str(root/"cache.sqlite3"))
            cfg.setValue("location",str(root))
            w=Explorer(cfg)
            try:
                w.show()
                wait_for(lambda: not w.workers)
                w.show_archive_matches(w.views[0])
                wait_for(lambda: not w.workers)
                self.assertFalse(w.archive_pairs)
                self.assertFalse(w.containment_buttons[0].isChecked())
                w.containment_buttons[0].click()
                wait_for(lambda: not w.workers)
                self.assertEqual(len(w.archive_pairs),1)
                self.assertTrue(w.archive_pairs[0].contained and w.archive_pairs[0].verified)
                # Both panes may show archive matches but with independent options.
                w.show_archive_matches(w.views[1])
                wait_for(lambda: not w.workers)
                self.assertEqual(w.proxies[0].rowCount(), 2)
                self.assertEqual(w.proxies[1].rowCount(), 0)
                self.assertTrue(w.archive_footers[0].isVisible())
                self.assertTrue(w.archive_footers[1].isVisible())
                self.assertFalse(w.containment_buttons[1].isChecked())
                self.assertIn("0 verified / 0 pairs", w.counts[1].text())
                self.assertIs(w.containment_buttons[0].parentWidget(), w.archive_footers[0])
                self.assertGreater(w.archive_footers[0].geometry().top(), w.views[0].geometry().top())
                with patch_recycle(return_value=[]) as recycler:
                    w.cleanup_archive_pairs(view=w.views[1])
                    recycler.assert_not_called()
                    w.cleanup_archive_pairs(view=w.views[0])
                    wait_for(lambda: not w.workers and not w.duplicate_cleanup)
                    recycler.assert_called_once_with([str(z)])
                self.assertEqual((folder/"data.txt").read_text(),"data")
                self.assertEqual((folder/"extra.txt").read_text(),"extra")
            finally:
                w.close()
                w.pool.waitForDone(5000)
                app.processEvents()
