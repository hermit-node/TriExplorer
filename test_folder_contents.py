import os
os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
import sqlite3
from contextlib import closing
import tempfile
import unittest
import time
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication
from explorer import Explorer
import folder_sizes
from test_explorer import wait_for
from file_types import normalized
from panels import SORT_ROLE

app = QApplication.instance() or QApplication([])

class FolderContentsTests(unittest.TestCase):
    def test_legacy_cache_migrates_without_guessing_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root/"folder"
            folder.mkdir()
            db = root/"cache.db"
            with closing(sqlite3.connect(db)) as c, c:
                c.execute("CREATE TABLE folder_sizes (path TEXT PRIMARY KEY, bytes INTEGER NOT NULL, checked REAL NOT NULL, modified REAL NOT NULL)")
                c.execute("INSERT INTO folder_sizes VALUES (?,?,?,?)",(folder_sizes.key(folder),0,time.time(),folder.stat().st_mtime))
            value = folder_sizes.lookup(db,[(str(folder),folder.stat().st_mtime)])[folder_sizes.key(folder)]
            self.assertEqual(value.size,0)
            self.assertIsNone(value.items)
            self.assertFalse(value.empty)
            self.assertEqual(folder_sizes.refresh(db,[str(folder)]),[])
            value=folder_sizes.lookup(db,[(str(folder),folder.stat().st_mtime)])[folder_sizes.key(folder)]
            self.assertTrue(value.empty)
            self.assertEqual((value.files,value.directories,value.other),(0,0,0))

    def test_counts_distinguish_zero_bytes_from_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            folder=root/"folder"; folder.mkdir()
            (folder/"zero.txt").touch()
            (folder/".hidden").write_bytes(b"x"*32)
            child=folder/"child"; child.mkdir()
            (child/"data").write_bytes(b"x"*128)
            (folder/".triextract-test").mkdir()
            (folder/".triextract-test"/"ignored").write_bytes(b"x"*1000)
            db=root/"cache.db"
            self.assertEqual(folder_sizes.refresh(db,[str(folder)]),[])
            values=folder_sizes.lookup(db,[(str(x),x.stat().st_mtime) for x in (folder,child)])
            v=values[folder_sizes.key(folder)]
            self.assertEqual((v.files,v.directories,v.other,v.size),(2,2,0,160))
            self.assertFalse(v.empty)
            self.assertEqual(values[folder_sizes.key(child)].items,1)

    def test_modes_and_live_empty_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            base=root/"files"; base.mkdir()
            empty=base/"Empty"; empty.mkdir()
            zero=base/"Zero"; zero.mkdir()
            (zero/"zero.txt").touch()
            nested=base/"Nested"; nested.mkdir()
            (nested/"sub").mkdir()
            hidden=base/"Hidden"; hidden.mkdir()
            (hidden/".secret").touch()
            excluded=base/"Excluded"; excluded.mkdir()
            (excluded/".triextract-test").mkdir()
            cfg=QSettings(str(root/"settings.ini"),QSettings.IniFormat)
            cfg.setValue("cache/sqlite_path",str(root/"cache.db"))
            cfg.setValue("location",str(base))
            cfg.setValue("pane/0/mode","empty_folders")
            cfg.setValue("pane/1/mode","folders")
            w=Explorer(cfg)
            try:
                w.show()
                wait_for(lambda: not w.workers)
                self.assertEqual(w.proxies[0].rowCount(),1)
                self.assertEqual(w.proxies[0].index(0,0).data(),"Empty")
                self.assertEqual(w.proxies[0].index(0,4).data(),"0 F / 0 D")
                self.assertEqual(w.proxies[1].rowCount(),5)
                self.assertFalse(w.views[0].isColumnHidden(4))
                self.assertFalse(w.views[1].isColumnHidden(4))
                w.views[1].sortByColumn(4,Qt.DescendingOrder)
                self.assertEqual(w.proxies[1].index(0,4).data(SORT_ROLE),1)
                self.assertEqual(w.proxies[1].index(4,4).data(SORT_ROLE),0)
                # A failed re-scan cannot leave a previously confirmed empty row.
                with patch("folder_sizes.scan_tree",side_effect=PermissionError("fixture denied")):
                    w.refresh_folder_sizes(True,str(empty))
                    wait_for(lambda: not w.workers)
                    self.assertEqual(w.proxies[0].rowCount(),0)
                (empty/"new").touch()
                w.refresh_folder_sizes(True,str(empty))
                wait_for(lambda: not w.workers)
                self.assertEqual(w.proxies[0].rowCount(),0)
                (empty/"new").unlink()
                w.refresh_folder_sizes(True,str(empty))
                wait_for(lambda: not w.workers)
                self.assertEqual(w.proxies[0].rowCount(),1)
                w.mode_selectors[1].setCurrentIndex(w.mode_selectors[1].findData("all"))
                self.assertTrue(w.views[1].isColumnHidden(4))
                self.assertFalse(w.views[0].isColumnHidden(4))
            finally:
                w.close()
                w.pool.waitForDone(5000)
                app.processEvents()

    def test_alt_double_click_opens_child_not_parent(self):
        from PySide6.QtTest import QTest
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = root / "parent"
            base.mkdir()
            child = base / "child with spaces"
            child.mkdir()
            cfg = QSettings(str(root/"settings.ini"), QSettings.IniFormat)
            cfg.setValue("cache/sqlite_path",str(root/"cache.db"))
            cfg.setValue("location",str(base))
            w = Explorer(cfg)
            try:
                w.show()
                wait_for(lambda: not w.workers)
                view = w.views[0]
                idx = w.proxies[0].index(0,0)
                pos = view.visualRect(idx).center()
                with patch("controllers.file_actions.QDesktopServices.openUrl",return_value=True) as opener:
                    QTest.mouseDClick(view.viewport(),Qt.LeftButton,Qt.AltModifier,pos)
                    opener.assert_called_once()
                    self.assertEqual(normalized(opener.call_args.args[0].toLocalFile()),normalized(str(child)))
                    self.assertEqual(normalized(w.current),normalized(str(base)))
                w.double_click_entry(view,idx)
                wait_for(lambda: not w.workers)
                self.assertEqual(normalized(w.current),normalized(str(child)))
            finally:
                w.close()
                w.pool.waitForDone(5000)
                app.processEvents()
