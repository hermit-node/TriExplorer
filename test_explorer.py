"""Smoke/regression tests; all mutations occur in a temporary directory."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile
from pathlib import Path
from PySide6.QtCore import QSettings, Qt, QPointF, QMimeData, QUrl
from PySide6.QtWidgets import QApplication
import explorer as main
import appcore
import settings as app_settings
from services import recycle as recycle_service, archive as archive_service
from controllers import duplicate_actions, file_actions, preview

class RecyclePatch:
    # Update the references where recycle is looked up, not just one import alias.
    def __init__(self, *args, **kwargs):
        self.primary = patch.object(appcore, "recycle", *args, **kwargs)
        self.extra = []
    def start(self):
        mock = self.primary.start()
        try:
            for module in (duplicate_actions, file_actions, archive_service):
                p = patch.object(module, "recycle", new=mock)
                p.start()
                self.extra.append(p)
        except Exception:
            self.stop()
            raise
        return mock
    def stop(self):
        for p in reversed(self.extra):
            p.stop()
        self.extra.clear()
        self.primary.stop()
    def __enter__(self):
        return self.start()
    def __exit__(self, *_):
        self.stop()
        return False

def patch_recycle(*args, **kwargs):
    return RecyclePatch(*args, **kwargs)



app = QApplication.instance() or QApplication([])
app.setStyle("Fusion")
app.setStyleSheet(main.stylesheet.STYLE)

def wait_for(fn, seconds=8):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        if fn():
            return
        time.sleep(.01)
    raise AssertionError("Timed out")

class ExplorerTest(unittest.TestCase):
    def setUp(self):
        # Use a dedicated settings file, not the real Windows user registry.
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        ini = str(Path(temp.name) / "test.ini")
        factory = patch.object(app_settings, "QSettings", side_effect=lambda *args: QSettings(ini, QSettings.IniFormat))
        factory.start()
        self.addCleanup(factory.stop)
        portable = patch.object(app_settings, "PortableIni", side_effect=lambda *_: QSettings(ini, QSettings.IniFormat))
        portable.start()
        self.addCleanup(portable.stop)
        size_factory = patch.object(main.folder_sizes, "cache_path", return_value=Path(temp.name) / "sizes.sqlite3")
        size_factory.start()
        self.addCleanup(size_factory.stop)

    def test_inline_rename_sequence(self):
        from PySide6.QtTest import QTest
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/"Folder").mkdir()
            for name in ("a.txt","b.txt","c.txt"):
                (root/name).write_text(name)
            w=main.Explorer()
            w.show()
            try:
                w.navigate(str(root))
                wait_for(lambda: not w.workers)
                view=w.views[0]
                def row(name):
                    return next(view.model().index(i,0) for i in range(view.model().rowCount())
                                if view.model().index(i,0).data()==name)
                # Actual mouse double-click signal through the Qt view.
                w.double_click_entry(view,row("a.txt"))
                self.assertEqual(w.rename_editor.selectedText(),"a")
                w.rename_editor.setText("z.txt")
                QTest.keyClick(w.rename_editor,Qt.Key_Return)
                wait_for(lambda: not w.rename_busy and w.rename_editor is not None and w.rename_editor.text()=="b.txt")
                self.assertTrue((root/"z.txt").exists())
                self.assertFalse((root/"a.txt").exists())
                w.rename_editor.setText("c.txt")
                QTest.keyClick(w.rename_editor,Qt.Key_Return)
                wait_for(lambda: not w.rename_busy)
                self.assertEqual(w.rename_editor.text(),"c.txt")
                self.assertEqual(Path(w.rename_path).name,"b.txt")
                self.assertTrue((root/"b.txt").exists())
                w.rename_editor.setText("bb.txt")
                QTest.keyClick(w.rename_editor,Qt.Key_Return)
                wait_for(lambda: not w.rename_busy and w.rename_editor is not None and Path(w.rename_path).name=="c.txt")
                QTest.keyClick(w.rename_editor,Qt.Key_Escape)
                wait_for(lambda: not w.workers)
                self.assertIsNone(w.rename_editor)
                self.assertEqual({p.name for p in root.iterdir()},{"Folder","z.txt","bb.txt","c.txt"})
                w.double_click_entry(view,row("Folder"))
                wait_for(lambda: not w.workers)
                self.assertEqual(main.normalized(w.current),main.normalized(str(root/"Folder")))
            finally:
                w.close()
                w.pool.waitForDone(5000)
                app.processEvents()

    def test_collapsible_image_preview(self):
        from PySide6.QtGui import QImage, QColor
        from PySide6.QtCore import QItemSelectionModel
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for name,color in (("a.png","red"),("b.png","blue")):
                im=QImage(400,200,QImage.Format_RGB32)
                im.fill(QColor(color))
                self.assertTrue(im.save(str(root/name)))
            w=main.Explorer()
            w.show()
            try:
                w.navigate(str(root))
                wait_for(lambda: not w.workers)
                view=w.views[0]
                def select(row):
                    i=view.model().index(row,0)
                    view.setCurrentIndex(i)
                    view.selectionModel().select(i,QItemSelectionModel.ClearAndSelect|QItemSelectionModel.Rows)
                    w.set_active(view)
                with patch.object(preview,"read_image_preview",wraps=preview.read_image_preview) as read:
                    self.assertFalse(w.preview_open)
                    select(0)
                    app.processEvents()
                    read.assert_not_called()
                    w.preview_button.setChecked(True)
                    wait_for(lambda: w.preview_label.source is not None and not w.workers)
                    self.assertEqual(read.call_count,1)
                    self.assertIn("400",w.preview_details.text())
                    self.assertEqual(w.preview_label.source.size().width(),400)
                    select(1)
                    w.preview_button.setChecked(False)
                    wait_for(lambda: not w.workers)
                    self.assertIsNone(w.preview_label.source)
                    self.assertFalse(w.preview_timer.isActive())
                    self.assertFalse(w.preview_panel.isVisible())
                    self.assertEqual(read.call_count,1)
                    select(0)
                    w.preview_button.setChecked(True)
                    wait_for(lambda: w.preview_label.source is not None and not w.workers)
                    self.assertEqual(read.call_count,2)
                    w.preview_button.setChecked(False)
                w.close()
                w2=main.Explorer()
                try:
                    wait_for(lambda: not w2.workers)
                    self.assertFalse(w2.preview_open)
                finally:
                    w2.close()
                    w2.pool.waitForDone(5000)
            finally:
                w.close()
                w.pool.waitForDone(5000)
                app.processEvents()

    def test_portable_runtime_locations(self):
        import runtime_paths
        from unittest.mock import patch
        import settings as settings_module
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            fake_executable = root / "TriExplorer.exe"
            with patch.object(runtime_paths.sys, "frozen", True, create=True), \
                 patch.object(runtime_paths.sys, "executable", str(fake_executable)):
                self.assertEqual(runtime_paths.application_directory(), root)
                self.assertEqual(settings_module.settings_file(), root / "settings.ini")
                cfg = QSettings(str(root / "settings.ini"), QSettings.IniFormat)
                cfg.setValue("cache/sqlite_path", "auto")
                self.assertEqual(settings_module.configured_cache_path(cfg),
                                 root / "TriExplorer.sqlite3")
                cfg.setValue("cache/sqlite_path", "subfolder/data.sqlite3")
                self.assertEqual(settings_module.configured_cache_path(cfg),
                                 root / "subfolder" / "data.sqlite3")
                cfg.sync()
                from portable_ini import PortableIni
                with patch.object(settings_module, "PortableIni", new=PortableIni):
                    settings_module.open_settings()
                cfg.sync()
                self.assertTrue((root / "settings.ini").is_file())
                self.assertEqual(cfg.value("meta/legacy_imported", False, type=bool), True)
            with patch.object(runtime_paths.sys, "_MEIPASS", str(root), create=True):
                self.assertEqual(runtime_paths.bundled_asset("assets", "triexplorer.ico"),
                                 root / "assets" / "triexplorer.ico")

    def test_taskbar_icon(self):
        import app_icons
        from PySide6.QtGui import QIcon
        self.assertEqual(app_icons.APP_ID, "KHzz.TriExplorer")
        self.assertTrue(app_icons.ICON_PATH.is_file())
        data = app_icons.ICON_PATH.read_bytes()
        self.assertEqual(data[:4], bytes([0, 0, 1, 0]))
        self.assertGreater(int.from_bytes(data[4:6], "little"), 5)
        icon = app_icons.app_icon()
        self.assertFalse(icon.isNull())
        for size in (16, 32, 48, 256):
            self.assertFalse(icon.pixmap(size, size).isNull())
        w = main.Explorer()
        try:
            self.assertFalse(w.windowIcon().isNull())
        finally:
            w.close()
            w.pool.waitForDone(5000)

    def test_portable_settings_and_panel_module(self):
        import panels
        from PySide6.QtCore import QSettings
        self.assertIs(main.Files, panels.Files)
        self.assertIs(main.PaneFilter, panels.PaneFilter)
        self.assertIs(main.FileView, panels.FileView)
        self.assertIs(main.PreviewLabel, panels.PreviewLabel)
        self.assertIs(main.create_table_pane, panels.create_table_pane)
        self.assertTrue(main.SETTINGS_PATH.is_absolute())
        self.assertEqual(main.SETTINGS_PATH.name, "settings.ini")
        old_name = appcore.NETWORK_TRASH
        self.addCleanup(setattr, appcore, "NETWORK_TRASH", old_name)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ini = QSettings(str(root / "preferences.ini"), QSettings.IniFormat)
            ini.setValue("app/name", "My Custom Explorer")
            ini.setValue("cache/sqlite_path", "data/cache.sqlite3")
            ini.setValue("network/trash_name", ".CustomRecycle")
            ini.setValue("pane/0/mode", "images")
            ini.setValue("pane/1/mode", "all")
            ini.sync()
            self.assertEqual(main.configured_cache_path(ini),
                             (main.SETTINGS_PATH.parent / "data" / "cache.sqlite3").resolve())
            self.assertTrue(main.configured_cache_path(ini).is_absolute())
            self.assertRaises(ValueError, lambda: (
                ini.setValue("network/trash_name", "../escape"),
                main.configure_network_trash(ini)
            ))
            ini.setValue("network/trash_name", ".CustomRecycle")
            main.configure_network_trash(ini)
            self.assertEqual(appcore.NETWORK_TRASH, ".CustomRecycle")
            self.assertIn(".customrecycle", main.folder_sizes.EXCLUDED)
            self.assertIn(".customrecycle", main.duplicates.SKIP_DIRS)
            a = root / "data.bin"
            a.write_bytes(b"abc")
            with patch.object(recycle_service, "network_root", return_value=root):
                self.assertFalse(main.recycle([str(a)]))
            self.assertFalse(a.exists())
            self.assertEqual(main.share_trash_entries(root)[0][1], "data.bin")
            main.restore_share_item(root, main.share_trash_entries(root)[0][2])
            self.assertEqual(a.read_bytes(), b"abc")
            # Supply the custom settings to the Explorer under test without
            # modifying the actual per-user INI or Windows registry.
            with patch.object(main, "open_settings", return_value=ini):
                w = main.Explorer()
                w.show()
                try:
                    wait_for(lambda: not w.workers)
                    self.assertEqual(w.windowTitle(), "My Custom Explorer")
                    self.assertEqual(w.size_db, main.configured_cache_path(ini))
                    self.assertEqual([p.mode for p in w.proxies], ["images", "all"])
                finally:
                    w.close()
                    w.pool.waitForDone(5000)
                    app.processEvents()

    def test_external_filter_definitions(self):
        import filters
        self.assertIs(main.PANE_MODES, filters.PANE_MODES)
        self.assertIs(main.CATEGORY_SUFFIXES, filters.CATEGORY_SUFFIXES)
        self.assertIs(main.ARCHIVE_SUFFIXES, filters.ARCHIVE_SUFFIXES)
        self.assertIs(main.archive_name, filters.archive_name)
        self.assertTrue(main.archive_name("backup.TAR.ZST"))
        self.assertTrue(main.archive_name("bundle.7Z"))
        self.assertFalse(main.archive_name("report.docx"))
        self.assertIn(".tar.zst", filters.CATEGORY_SUFFIXES["archives"])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "folder.zip").mkdir()
            for name in ("backup.TAR.ZST", "art.AVIF", "report.DOCX", "script.PY", "item.txt"):
                (root / name).write_bytes(b"test")
            entries = {e.name: e for e in main.scan(str(root))}
            self.assertFalse(entries["folder.zip"].archive)
            self.assertTrue(entries["backup.TAR.ZST"].archive)
            w = main.Explorer()
            w.show()
            try:
                w.navigate(str(root))
                wait_for(lambda: not w.workers)
                self.assertEqual(w.mode_selectors[0].count(), len(filters.PANE_MODES))
                def visible(mode):
                    selector = w.mode_selectors[0]
                    selector.setCurrentIndex(selector.findData(mode))
                    app.processEvents()
                    proxy = w.proxies[0]
                    return {proxy.index(row, 0).data() for row in range(proxy.rowCount())}
                self.assertEqual(visible("archives"), {"backup.TAR.ZST"})
                self.assertEqual(visible("images"), {"art.AVIF"})
                self.assertEqual(visible("documents"), {"report.DOCX", "item.txt"})
                self.assertEqual(visible("code"), {"script.PY"})
                self.assertEqual(visible("folders"), {"folder.zip"})
            finally:
                w.close()
                w.pool.waitForDone(5000)
                app.processEvents()

    def test_folder_size_cache(self):
        import folder_sizes
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "cache.sqlite3"
            a = root / "A"
            nested = a / "nested"
            nested.mkdir(parents=True)
            (a / "one.bin").write_bytes(b"x" * 1000)
            (nested / "two.bin").write_bytes(b"x" * 2000)
            (a / ".TriExplorer-Recycle").mkdir()
            (a / ".TriExplorer-Recycle" / "trashed").write_bytes(b"x" * 10000)
            b = root / "B"
            b.mkdir()
            (b / "zero.bin").touch()
            self.assertEqual(folder_sizes.lookup(db, [(str(a), a.stat().st_mtime)]), {})
            self.assertEqual(folder_sizes.refresh(db, [str(a), str(b)]), [])
            sizes = folder_sizes.lookup(db, [(str(a), a.stat().st_mtime), (str(nested), nested.stat().st_mtime),
                                               (str(b), b.stat().st_mtime)])
            self.assertEqual(sizes[folder_sizes.key(a)].size, 3000)
            self.assertEqual(sizes[folder_sizes.key(nested)].size, 2000)
            self.assertEqual(sizes[folder_sizes.key(b)].size, 0)
            self.assertFalse(sizes[folder_sizes.key(a)].stale)
            with patch.object(folder_sizes, "MAX_AGE", -1):
                self.assertTrue(folder_sizes.lookup(db, [(str(a), a.stat().st_mtime)])[folder_sizes.key(a)].stale)
            self.assertTrue(folder_sizes.lookup(db, [(str(a), a.stat().st_mtime + 1)])[folder_sizes.key(a)].stale)
            (nested / "two.bin").write_bytes(b"x" * 8000)
            self.assertEqual(folder_sizes.refresh(db, [str(a)]), [])
            self.assertEqual(folder_sizes.lookup(db, [(str(a), a.stat().st_mtime)])[folder_sizes.key(a)].size, 9000)
            other = root / "Other"
            other.mkdir()
            self.assertEqual(folder_sizes.refresh(db, [str(other)], lambda: True), [])
            self.assertEqual(folder_sizes.lookup(db, [(str(other), other.stat().st_mtime)]), {})

    def test_cached_folder_sizes_ui(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a = root / "A"
            b = root / "B"
            a.mkdir()
            b.mkdir()
            (a / "data").write_bytes(b"x" * 2048)
            (b / "data").write_bytes(b"x" * 800)
            w = main.Explorer()
            w.show()
            try:
                w.navigate(str(root))
                wait_for(lambda: not w.workers)
                self.assertEqual(w.model.folder_sizes[main.normalized(str(a))].size, 2048)
                self.assertEqual(w.model.folder_sizes[main.normalized(str(b))].size, 800)
                proxy = w.proxies[0]
                w.views[0].sortByColumn(1, Qt.DescendingOrder)
                self.assertEqual(proxy.index(0, 0).data(), "A")
                self.assertEqual(proxy.index(0, 1).data(), "2.0 KB")
                w.close()
                with patch.object(main.folder_sizes, "scan_tree", wraps=main.folder_sizes.scan_tree) as scanner:
                    w2 = main.Explorer()
                    try:
                        wait_for(lambda: not w2.workers)
                        self.assertEqual(w2.model.folder_sizes[main.normalized(str(a))].size, 2048)
                        scanner.assert_not_called()
                        (a / "data").write_bytes(b"x" * 4096)
                        w2.refresh_folder_sizes(True, str(a))
                        wait_for(lambda: not w2.workers)
                        self.assertEqual(w2.model.folder_sizes[main.normalized(str(a))].size, 4096)
                        self.assertEqual(scanner.call_count, 1)
                    finally:
                        w2.close()
                        w2.pool.waitForDone(5000)
            finally:
                w.close()
                w.pool.waitForDone(5000)
                app.processEvents()

    def test_resizable_pins_and_scan_progress(self):
        import duplicates
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "small.txt").write_bytes(b"a")
            (root / "small_2.txt").write_bytes(b"a")
            (root / "large.txt").write_bytes(b"b"*4096)
            (root / "large_2.txt").write_bytes(b"b"*4096)
            matches = []
            result = duplicates.find_duplicates(str(root),
                matches=lambda groups, done, total: matches.append((groups[0][0].size, done, total)))
            self.assertEqual(matches, [(1, 2, 4), (4096, 4, 4)])
            self.assertEqual(len(result.groups), 2)
            w = main.Explorer()
            w.resize(1000, 700)
            w.show()
            try:
                wait_for(lambda: not w.workers)
                self.assertEqual(w.side_splitter.orientation(), Qt.Vertical)
                self.assertEqual(w.side_splitter.count(), 2)
                w.side_splitter.setSizes([320, 200])
                app.processEvents()
                before = w.side_splitter.sizes()
                self.assertGreater(before[0], before[1])
                w.add_pin(str(root))
                self.assertEqual(w.side_splitter.sizes(), before)
                w.close()
                w2 = main.Explorer()
                w2.show()
                try:
                    wait_for(lambda: not w2.workers)
                    self.assertEqual(w2.side_splitter.sizes(), before)
                finally:
                    w2.close()
                    w2.pool.waitForDone(5000)
            finally:
                w.close()
                w.pool.waitForDone(5000)

    def test_smb_duplicate_metadata(self):
        import duplicates
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a = root / "sample.zip"
            b = root / "sample_2.zip"
            b.write_bytes(b"identical")
            a.write_bytes(b"identical")
            real = duplicates.os.fstat
            def network_fstat(fd):
                st = real(fd)
                return SimpleNamespace(st_dev=st.st_dev + 1, st_ino=st.st_ino + 1,
                    st_size=st.st_size, st_mtime_ns=st.st_mtime_ns + 100,
                    st_ctime_ns=st.st_ctime_ns + 100)
            with patch.object(duplicates.os, "fstat", side_effect=network_fstat):
                progress = []
                result = duplicates.find_duplicates(str(root),
                    progress=lambda n, total: progress.append((n, total)))
                self.assertEqual(result.errors, [])
                self.assertEqual(len(result.groups), 1)
                self.assertEqual(Path(result.groups[0][0].path), a)
                self.assertEqual(Path(result.groups[0][1][0].path), b)
                self.assertEqual(progress[-1], (2, 2))
            item = duplicates.record(a)
            a.write_bytes(b"modified")
            with self.assertRaises(ValueError):
                duplicates.hash_file(item)

    def test_sample_and_hash_cache(self):
        import duplicates
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            n = 1024*1024
            (d/"same.bin").write_bytes(b"a"*n)
            (d/"same_2.bin").write_bytes(b"a"*n)
            (d/"decoy.bin").write_bytes(b"b"*n)
            (d/"different-size.bin").write_bytes(b"a"*(n+1))
            db = d / "cache.sqlite3"
            with patch.object(duplicates,"hash_file",wraps=duplicates.hash_file) as reader:
                result = duplicates.find_duplicates(str(d),cache_db=db)
                self.assertEqual(len(result.groups),1)
                self.assertEqual(reader.call_count,2)
                reader.reset_mock()
                result = duplicates.find_duplicates(str(d),cache_db=db)
                self.assertEqual(len(result.groups),1)
                reader.assert_not_called()
            # Destructive scans deliberately don't reuse browsing hashes.
            with patch.object(duplicates,"hash_file",wraps=duplicates.hash_file) as reader:
                self.assertEqual(len(duplicates.find_duplicates(str(d)).groups),1)
                self.assertEqual(reader.call_count,2)
            (d/"same_2.bin").write_bytes(b"z"*n)
            self.assertFalse(duplicates.find_duplicates(str(d),cache_db=db).groups)

    def test_zip_folder_matches(self):
        import archive_matches
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp)
            folder=d/"Extracted"
            nested=folder/"inner"
            nested.mkdir(parents=True)
            payload=bytes(range(256))*4096
            (nested/"data.bin").write_bytes(payload)
            good=d/"different_name.zip"
            wrong=d/"wrong.zip"
            extra=d/"extra.zip"
            for zpath,data,more in [(good,payload,False),(wrong,b"x"*len(payload),False),(extra,payload,True)]:
                with zipfile.ZipFile(zpath,"w",compression=zipfile.ZIP_STORED) as z:
                    z.writestr("wrapper/inner/data.bin",data)
                    if more:
                        z.writestr("wrapper/extra.txt","extra")
            # Wrapped roots may differ; extra files or identical-size changed contents do not match.
            result=archive_matches.compare(str(good),str(folder),main.zip_members)
            self.assertTrue(result.verified)
            self.assertIsNone(archive_matches.compare(str(wrong),str(folder),main.zip_members))
            self.assertIsNone(archive_matches.compare(str(extra),str(folder),main.zip_members))
            matches,errors=archive_matches.find_matches([str(folder)],
                [(str(z),z.stat().st_size) for z in (good,wrong,extra)], {}, d/"cache.sqlite3",main.zip_members)
            self.assertEqual(errors,[])
            self.assertEqual([m.archive for m in matches],[str(good)])
            # Password-protected ZIPs are candidates until verified using contents.
            import pyzipper
            encrypted=d/"Extracted.zip"
            with pyzipper.AESZipFile(encrypted,"w",compression=pyzipper.ZIP_DEFLATED,
                                     encryption=pyzipper.WZ_AES) as z:
                z.setpassword(b"test")
                z.writestr("inner/data.bin",payload)
            candidate=archive_matches.compare(str(encrypted),str(folder),main.zip_members)
            self.assertFalse(candidate.verified)
            self.assertTrue(archive_matches.compare(str(encrypted),str(folder),main.zip_members,b"test").verified)

    def test_archive_cleanup_and_password(self):
        import pyzipper
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            pairs=[]
            for name, payload, pwd in [("One",b"a"*2048,b"alpha"),("Two",b"b"*3072,b"alpha"),
                                        ("Three",b"c"*4096,b"beta"),("Four",b"d"*5120,b"gamma")]:
                folder=root/name
                folder.mkdir()
                (folder/"data.bin").write_bytes(payload)
                path=root/(name+".zip")
                with pyzipper.AESZipFile(path,"w",compression=pyzipper.ZIP_STORED,
                                          encryption=pyzipper.WZ_AES) as z:
                    z.setpassword(pwd)
                    z.writestr("data.bin",payload)
                pairs.append(path)
            w=main.Explorer()
            w.show()
            prompts=[]
            warnings=[]
            def ask(path,rejected):
                name=Path(path).stem
                prompts.append((name,rejected))
                return {"One":b"alpha","Two":b"alpha","Three":b"beta","Four":None}[name]
            w.ask_zip_password=ask
            w.warn=warnings.append
            try:
                w.navigate(str(root))
                wait_for(lambda: not w.workers)
                w.show_archive_matches(w.views[0])
                wait_for(lambda: not w.workers)
                self.assertEqual(w.proxies[0].rowCount(),8)
                names=[w.proxies[0].index(i,0).data() for i in range(8)]
                self.assertEqual(names,sorted(names,key=str.casefold))
                self.assertEqual(len(w.archive_pairs),4)
                self.assertFalse(any(m.verified for m in w.archive_pairs))
                # Use a deterministic sequence to test password reuse and cancellation.
                w.archive_pairs.sort(key=lambda m:["One","Two","Three","Four"].index(Path(m.archive).stem))
                def recycle_fixture(paths):
                    for path in paths:
                        main.share_recycle(path,root)
                    return []
                with patch_recycle(side_effect=recycle_fixture):
                    w.cleanup_archive_pairs()
                    wait_for(lambda: not w.duplicate_cleanup and not w.workers)
                self.assertEqual(prompts,[("One",False),("Three",True),("Four",True)])
                self.assertEqual(w.zip_password,b"beta")
                self.assertEqual([p.exists() for p in pairs],[False,False,False,True])
                self.assertTrue(all((root/p.stem/"data.bin").is_file() for p in pairs))
                self.assertEqual(w.proxies[0].rowCount(),2)
                self.assertEqual(len(w.archive_pairs),1)
                self.assertEqual(len(main.share_trash_entries(root)),3)
                self.assertEqual(len(warnings),1)
            finally:
                w.close()
                w.pool.waitForDone(5000)
                app.processEvents()

    def test_zip_folder_view(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            folder=root/"Folder"
            folder.mkdir()
            (folder/"data.bin").write_bytes(b"x"*20000)
            with zipfile.ZipFile(root/"different.zip","w") as z:
                z.writestr("data.bin",b"x"*20000)
            w=main.Explorer()
            w.show()
            try:
                w.navigate(str(root))
                wait_for(lambda: not w.workers)
                w.show_archive_matches(w.views[0])
                wait_for(lambda: not w.workers)
                self.assertEqual(w.proxies[0].rowCount(),2)
                self.assertIn("1 verified",w.counts[0].text())
                self.assertIn("SHA-256",w.model.archive_notes[main.normalized(str(folder))])
                self.assertTrue((root/"different.zip").exists())
                (folder/"data.bin").write_bytes(b"y"*20000)
                warnings=[]
                w.warn=warnings.append
                with patch_recycle() as recycler:
                    w.cleanup_archive_pairs()
                    wait_for(lambda: not w.duplicate_cleanup and not w.workers)
                    recycler.assert_not_called()
                self.assertTrue((root/"different.zip").exists())
                self.assertEqual(w.proxies[0].rowCount(),0)
                self.assertTrue(warnings)
            finally:
                w.close()
                w.pool.waitForDone(5000)

    def test_duplicate_engine(self):
        import duplicates
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            files = {
                "thing.txt": b"abc",
                "thing - Copy.txt": b"abc",
                "same-size-not-duplicate.txt": b"xyz",
                "different-size.txt": b"longer",
                "unique.txt": b"unique-contents",
                "second.txt": b"12345",
                "another.txt": b"12345",
            }
            for name, content in files.items():
                (root / name).write_bytes(content)
            sub = root / "sub"
            sub.mkdir()
            (sub / "thing.txt").write_bytes(b"abc")
            (root / ".hidden.txt").write_bytes(b"abc")
            trash = root / appcore.NETWORK_TRASH
            trash.mkdir()
            (trash / "dont-touch.txt").write_bytes(b"abc")
            with patch.object(duplicates, "hash_file", wraps=duplicates.hash_file) as hashing:
                result = duplicates.find_duplicates(str(root))
                self.assertEqual(len(result.groups), 2, repr(result))
                self.assertEqual(hashing.call_count, 5)
            group = next(g for g in result.groups if g[0].size == 3)
            self.assertEqual(Path(group[0].path).name, "thing.txt")
            self.assertEqual([Path(p.path).name for p in group[1]], ["thing - Copy.txt"])
            result = duplicates.find_duplicates(str(root), recursive=True,
                                               preferred=str(root / "thing - Copy.txt"))
            self.assertEqual(len(result.groups), 1)
            self.assertEqual(Path(result.groups[0][0].path).name, "thing - Copy.txt")
            self.assertEqual(len(result.groups[0][1]), 2)
            # A changed file is never recycled based on an old SHA result.
            (sub / "thing.txt").write_bytes(b"changed")
            def recycle_fixture(paths):
                for path in paths:
                    main.share_recycle(path, root)
                return []
            count, errors = duplicates.remove_duplicates(result, recycle_fixture)
            self.assertEqual(count, 1)
            self.assertEqual(len(errors), 1)
            self.assertTrue((sub / "thing.txt").exists())
            self.assertTrue((root / "thing - Copy.txt").exists())
            self.assertFalse((root / "thing.txt").exists())
            self.assertTrue((trash / "dont-touch.txt").exists())
            # If the keeper vanishes, preserve every remaining candidate.
            result = duplicates.find_duplicates(str(root))
            keep, copies = result.groups[0]
            Path(keep.path).unlink()
            count, errors = duplicates.remove_duplicates(result, recycle_fixture)
            self.assertEqual(count, 0)
            self.assertTrue(errors)
            self.assertTrue(all(Path(copy.path).exists() for copy in copies))

    def test_duplicate_view_and_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "keep.txt").write_bytes(b"duplicate")
            (root / "keep (1).txt").write_bytes(b"duplicate")
            (root / "not-match.txt").write_bytes(b"different")
            w = main.Explorer()
            w.show()
            try:
                w.navigate(str(root))
                wait_for(lambda: not w.workers)
                w.show_duplicates(w.views[0])
                wait_for(lambda: not w.workers)
                self.assertEqual(w.proxies[0].rowCount(), 2)
                self.assertEqual(w.proxies[1].rowCount(), 0)
                with patch.object(main.duplicates, "find_duplicates", wraps=main.duplicates.find_duplicates) as finder:
                    w.refresh_from_watcher()
                    wait_for(lambda: not w.workers)
                    finder.assert_not_called()
                    self.assertEqual(w.proxies[0].rowCount(), 2)
                with patch_recycle( side_effect=lambda paths: [main.share_recycle(p, root) for p in paths] and []):
                    w.cleanup_duplicates(preferred=str(root / "keep (1).txt"))
                    wait_for(lambda: not w.duplicate_cleanup and not w.workers)
                self.assertTrue((root / "keep (1).txt").exists())
                self.assertTrue((root / "not-match.txt").exists())
                self.assertFalse((root / "keep.txt").exists())
                self.assertEqual(w.proxies[0].rowCount(), 0)
                self.assertEqual(main.share_trash_entries(root)[0][1], "keep.txt")
            finally:
                w.close()
                w.pool.waitForDone(5000)
                app.processEvents()

    def test_network_recycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "sub"
            folder.mkdir()
            f = folder / "sample.zip"
            f.write_bytes(b"fixture")
            with patch.object(recycle_service, "network_root", return_value=root):
                self.assertEqual(main.recycle([str(f)]), [])
                self.assertFalse(f.exists())
                records = main.share_trash_entries(root)
                self.assertEqual(records[0][1], "sub/sample.zip")
                self.assertFalse(any(e.name == appcore.NETWORK_TRASH for e in main.scan(str(root))))
                key = records[0][2]
                f.write_bytes(b"collision")
                with self.assertRaises(FileExistsError):
                    main.restore_share_item(root, key)
                self.assertEqual(f.read_bytes(), b"collision")
                f.unlink()
                main.restore_share_item(root, key)
                self.assertEqual(f.read_bytes(), b"fixture")
                self.assertEqual(main.recycle([str(folder)]), [])
                self.assertFalse(folder.exists())
                main.restore_share_item(root, main.share_trash_entries(root)[0][2])
                self.assertEqual(f.read_bytes(), b"fixture")
                self.assertEqual(main.share_trash_entries(root), [])
                self.assertTrue(main.recycle([str(root / appcore.NETWORK_TRASH)]))

    def test_operations(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            a, b = d / "A", d / "B"
            a.mkdir()
            b.mkdir()
            (a / "item.txt").write_text("example")
            self.assertEqual(main.transfer([str(a)], str(b), False), [])
            self.assertEqual((b / "A" / "item.txt").read_text(), "example")
            self.assertTrue(main.transfer([str(a)], str(b), False))
            self.assertTrue(main.transfer([str(a)], str(a), False))
            self.assertEqual(main.transfer([str(a / "item.txt")], str(b), True), [])
            self.assertFalse((a / "item.txt").exists())
            z = d / "test.zip"
            self.assertEqual(main.make_zip([str(b / "A")], str(z)), [])
            dest = d / "out"
            self.assertEqual(main.unzip(str(z), str(dest)), [])
            self.assertEqual((dest / "A" / "item.txt").read_text(), "example")
            with self.assertRaises(FileExistsError):
                main.unzip(str(z), str(dest))
            evil = d / "evil.zip"
            with zipfile.ZipFile(evil, "w") as f:
                f.writestr("../outside.txt", "bad")
            with self.assertRaises(ValueError):
                main.unzip(str(evil), str(d / "bad"))
            self.assertFalse((d / "bad").exists())

    def test_smart_zip(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            def create(name, entries):
                path = d / name
                with zipfile.ZipFile(path, "w") as z:
                    for filename in entries:
                        z.writestr(filename, "" if filename.endswith("/") else "content")
                return path
            for filename, entries, expected, files in [
                ("different.zip", ["Project/", "Project/main.py", "Project/src/a.py"], "Project", ["main.py", "src/a.py"]),
                ("implicit.zip", ["Root/a.txt", "Root/b.txt"], "Root", ["a.txt", "b.txt"]),
                ("flat.ZIP", ["a.txt", "b.txt"], "flat", ["a.txt", "b.txt"]),
                ("mixed.zip", ["folder/a.txt", "readme.md"], "mixed", ["folder/a.txt", "readme.md"]),
                ("multiple.zip", ["a/a.txt", "b/b.txt"], "multiple", ["a/a.txt", "b/b.txt"]),
                ("single.zip", ["one.txt"], "single", ["one.txt"]),
                ("empty-folder.zip", ["EmptyFolder/"], "EmptyFolder", []),
                ("empty.zip", [], "empty", []),
                ("backslash.zip", ["Win" + chr(92) + "a.txt"], "Win", ["a.txt"]),
            ]:
                path = create(filename, entries)
                before = path.read_bytes()
                out = main.smart_unzip(str(path), str(d))
                self.assertEqual(out, d / expected)
                self.assertTrue(out.is_dir())
                for file in files:
                    self.assertEqual((out / file).read_text(), "content")
                self.assertEqual(path.read_bytes(), before)
                with self.assertRaises(FileExistsError):
                    main.smart_unzip(str(path), str(d))
            for filename, entries in [
                ("traversal.zip", ["../outside"]),
                ("absolute.zip", ["/outside"]),
                ("drive.zip", ["C:/outside"]),
                ("conflict.zip", ["root/file", "root/file/child"]),
                ("case.zip", ["root/File", "root/file"]),
                ("reserved.zip", ["CON.txt"]),
            ]:
                path = create(filename, entries)
                before = set(d.iterdir())
                with self.assertRaises(ValueError):
                    main.smart_unzip(str(path), str(d))
                self.assertEqual(set(d.iterdir()), before)
            invalid = create("broken.zip", ["Bad/data.txt"])
            payload = bytearray(invalid.read_bytes())
            offset = payload.index(b"content")
            payload[offset] ^= 1
            invalid.write_bytes(payload)
            with self.assertRaises(zipfile.BadZipFile):
                main.smart_unzip(str(invalid), str(d))
            self.assertFalse((d / "Bad").exists())
            self.assertFalse(list(d.glob(".triextract-*")))

    def test_extract_then_recycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            valid = d / "valid.zip"
            bad = d / "bad.zip"
            blocked = d / "blocked.zip"
            for path, member in [(valid, "Root/a.txt"), (bad, "../evil"), (blocked, "Existing/x")]:
                with zipfile.ZipFile(path, "w") as z:
                    z.writestr(member, "value")
            (d / "Existing").mkdir()
            def recycle_fixture(paths):
                self.assertEqual(paths, [str(valid)])
                self.assertEqual((d / "Root" / "a.txt").read_text(), "value")
                valid.unlink()
                return []
            with patch_recycle( side_effect=recycle_fixture) as recycle:
                errors = main.extract_archives([str(bad), str(valid), str(blocked)], str(d), True)
                self.assertEqual(len(errors), 2)
                recycle.assert_called_once_with([str(valid)])
                self.assertFalse(valid.exists())
                self.assertTrue(bad.exists() and blocked.exists())
            preserve = d / "preserve.zip"
            with zipfile.ZipFile(preserve, "w") as z:
                z.writestr("a.txt", "value")
            with patch_recycle() as recycle:
                self.assertEqual(main.extract_archives([str(preserve)], str(d)), [])
                recycle.assert_not_called()
            self.assertTrue(preserve.exists())
            failed_recycle = d / "failed-recycle.zip"
            with zipfile.ZipFile(failed_recycle, "w") as z:
                z.writestr("a.txt", "value")
            with patch_recycle( return_value=["Access denied"]):
                errors = main.extract_archives([str(failed_recycle)], str(d), True)
            self.assertIn("extracted successfully", errors[0])
            self.assertTrue(failed_recycle.exists())
            self.assertTrue((d / "failed-recycle" / "a.txt").is_file())

    def test_password_queue(self):
        import pyzipper
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            QSettings.setDefaultFormat(QSettings.IniFormat)
            QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(d / "settings"))
            base = d / "sample"
            base.mkdir()
            def create(name, password):
                path = base / (name + ".zip")
                if password:
                    with pyzipper.AESZipFile(path, "w", compression=pyzipper.ZIP_DEFLATED,
                                             encryption=pyzipper.WZ_AES) as z:
                        z.setpassword(password)
                        z.writestr("data.txt", name)
                else:
                    with zipfile.ZipFile(path, "w") as z:
                        z.writestr("data.txt", name)
                return str(path)
            a = create("one", b"alpha")
            b = create("two", b"alpha")
            c = create("plain", None)
            e = create("three", b"beta")
            f = create("four", b"beta")
            g = create("skip", b"gamma")
            h = create("five", b"beta")
            w = main.Explorer()
            w.show()
            try:
                w.navigate(str(base))
                wait_for(lambda: w.proxies[1].rowCount() == 7)
                prompts = []
                replies = iter([b"alpha", b"wrong", b"beta", None])
                def ask(path, rejected):
                    prompts.append((Path(path).stem, rejected))
                    return next(replies)
                w.ask_zip_password = ask
                warnings = []
                w.warn = warnings.append
                recycled = []
                def recycle_fixture(paths):
                    for path in paths:
                        self.assertTrue((Path(path).with_suffix("") / "data.txt").is_file())
                        Path(path).unlink()
                        recycled.append(Path(path).stem)
                    return []
                with patch_recycle( side_effect=recycle_fixture):
                    w.do_extract([a,b,c], str(base), True)
                    wait_for(lambda: not w.extract_running)
                    self.assertEqual(w.zip_password, b"alpha")
                    w.do_extract([e,f,g,h], str(base), True)
                    wait_for(lambda: not w.extract_running)
                self.assertEqual(prompts, [("one", False), ("three", True), ("three", True), ("skip", True)])
                self.assertEqual(w.zip_password, b"beta")
                self.assertEqual(recycled, ["one","two","plain","three","four","five"])
                self.assertTrue(Path(g).exists())
                self.assertFalse((base / "skip").exists())
                self.assertEqual(len(warnings), 1)
                self.assertIn("skipped", warnings[0])
                self.assertFalse(list(base.glob(".triextract-*")))
                wait_for(lambda: not w.workers)
            finally:
                w.close()
                w.pool.waitForDone(5000)
                app.processEvents()

    def test_archive_drop(self):
        class Drop:
            def __init__(self, source, paths, position):
                self._source = source
                self._mime = QMimeData()
                self._mime.setUrls([QUrl.fromLocalFile(str(p)) for p in paths])
                self._position = QPointF(position)
                self.accepted = False
            def source(self): return self._source
            def mimeData(self): return self._mime
            def position(self): return self._position
            def modifiers(self): return Qt.ShiftModifier
            def proposedAction(self): return Qt.MoveAction
            def setDropAction(self, action): self.action = action
            def accept(self): self.accepted = True
            def ignore(self): self.accepted = False

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            QSettings.setDefaultFormat(QSettings.IniFormat)
            QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(d / "settings"))
            base = d / "sample"
            base.mkdir()
            (base / "Target").mkdir()
            archives = []
            for name, member in [("wrapped.zip", "Original/main.py"), ("flat.zip", "a.txt"), ("target.zip", "Inside/b.txt")]:
                path = base / name
                with zipfile.ZipFile(path, "w") as z:
                    z.writestr(member, "data")
                archives.append(path)
            w = main.Explorer()
            w.show()
            try:
                w.navigate(str(base))
                wait_for(lambda: w.proxies[0].rowCount() == 1 and w.proxies[1].rowCount() == 3)
                left, right = w.views
                expected_outputs = {
                    main.normalized(str(archives[0])): base / "Original" / "main.py",
                    main.normalized(str(archives[1])): base / "flat" / "a.txt",
                    main.normalized(str(archives[2])): base / "Target" / "Inside" / "b.txt",
                }
                def recycle_fixture(paths):
                    for path in paths:
                        if not expected_outputs[main.normalized(path)].is_file():
                            return ["Output not published before recycle"]
                        Path(path).unlink()
                    return []
                warnings = []
                w.warn = warnings.append
                recycler = patch_recycle( side_effect=recycle_fixture)
                recycler.start()
                self.addCleanup(recycler.stop)
                e = Drop(right, archives[:2], left.viewport().rect().bottomRight())
                left.dropEvent(e)
                self.assertTrue(e.accepted)
                self.assertEqual(e.action, Qt.CopyAction)
                wait_for(lambda: (base / "Original" / "main.py").exists() and (base / "flat" / "a.txt").exists())
                wait_for(lambda: w.proxies[0].rowCount() == 3)
                index = next(w.proxies[0].index(i, 0) for i in range(w.proxies[0].rowCount())
                             if w.proxies[0].index(i, 0).data() == "Target")
                w.mode_selectors[1].setCurrentIndex(w.mode_selectors[1].findData("all"))
                w.mode_selectors[0].setCurrentIndex(w.mode_selectors[0].findData("all"))
                mixed = Drop(left, [archives[2], base / "Target"], right.viewport().rect().bottomRight())
                right.dropEvent(mixed)
                self.assertFalse(mixed.accepted)
                self.assertTrue(archives[2].exists())
                index = next(w.proxies[1].index(i, 0) for i in range(w.proxies[1].rowCount())
                             if w.proxies[1].index(i, 0).data() == "Target")
                e = Drop(left, archives[2:], right.visualRect(index).center())
                right.dropEvent(e)
                wait_for(lambda: (base / "Target" / "Inside" / "b.txt").exists())
                wait_for(lambda: not any(p.exists() for p in archives))
                self.assertEqual(warnings, [])
                wait_for(lambda: not w.workers)
            finally:
                w.close()
                w.pool.waitForDone(5000)
                app.processEvents()

    def test_independent_views(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            QSettings.setDefaultFormat(QSettings.IniFormat)
            QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(d / "settings"))
            base = d / "items"
            base.mkdir()
            for name in ("zFolder", "b.png", "folder.zip"):
                (base / name).mkdir()
            for name in ("a.txt", "g.JPG", "m.mp4", "n.mp3", "r.pdf", "s.py", "t.zip"):
                (base / name).write_text("test")
            w = main.Explorer()
            w.show()
            try:
                w.navigate(str(base))
                wait_for(lambda: not w.workers)
                def names(i):
                    proxy = w.proxies[i]
                    return [proxy.index(row, 0).data() for row in range(proxy.rowCount())]
                def mode(i, key):
                    selector = w.mode_selectors[i]
                    selector.setCurrentIndex(selector.findData(key))
                    app.processEvents()
                self.assertEqual(names(1), ["t.zip"])
                for key, expected in [
                    ("folders", {"zFolder", "b.png", "folder.zip"}),
                    ("images", {"g.JPG"}),
                    ("documents", {"a.txt", "r.pdf"}),
                    ("video", {"m.mp4"}),
                    ("audio", {"n.mp3"}),
                    ("code", {"s.py"}),
                    ("files", {"a.txt", "g.JPG", "m.mp4", "n.mp3", "r.pdf", "s.py", "t.zip"}),
                    ("archives", {"t.zip"}),
                ]:
                    mode(0, key)
                    self.assertEqual(set(names(0)), expected, key)
                    self.assertEqual(names(1), ["t.zip"])
                mode(0, "all")
                self.assertEqual(len(names(0)), 10)
                self.assertEqual(set(names(0)[:3]), {"zFolder", "b.png", "folder.zip"})
                mode(0, "alphabetical")
                self.assertEqual(names(0), sorted(names(0), key=str.casefold))
                mode(1, "images")
                self.assertEqual(names(1), ["g.JPG"])
                self.assertEqual(len(names(0)), 10)
                w.views[1].sortByColumn(3, Qt.DescendingOrder)
                w.close()
                w2 = main.Explorer()
                try:
                    wait_for(lambda: not w2.workers)
                    self.assertEqual([p.mode for p in w2.proxies], ["alphabetical", "images"])
                    self.assertEqual(w2.views[1].header().sortIndicatorSection(), 3)
                    self.assertEqual(w2.views[1].header().sortIndicatorOrder(), Qt.DescendingOrder)
                finally:
                    w2.close()
                    w2.pool.waitForDone(5000)
            finally:
                w.close()
                w.pool.waitForDone(5000)

    def test_quick_access_customization(self):
        from PySide6.QtWidgets import QMenu
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            QSettings.setDefaultFormat(QSettings.IniFormat)
            QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(d / "settings"))
            w = main.Explorer()
            try:
                wait_for(lambda: not w.workers)
                original = [w.quick.topLevelItem(i).data(0, main.PATH_ROLE)
                            for i in range(w.quick.topLevelItemCount())
                            if w.quick.topLevelItem(i).data(0, main.PATH_ROLE) is not None]
                for path in original:
                    menu = QMenu()
                    w.pin_action(menu, path)
                    self.assertEqual(menu.actions()[0].text(), "Unpin from Quick Access")
                    menu.actions()[0].trigger()
                self.assertFalse(w.visible_pins)
                menu = QMenu()
                w.pin_action(menu, str(d))
                menu.actions()[0].trigger()
                self.assertEqual(w.visible_pins, {main.normalized(str(d))})
                w.add_pin(str(d))
                self.assertEqual(len(w.pins), 1)
                w.close()
                w2 = main.Explorer()
                try:
                    wait_for(lambda: not w2.workers)
                    self.assertEqual(w2.visible_pins, {main.normalized(str(d))})
                    w2.unpin(str(d))
                    self.assertFalse(w2.visible_pins)
                    w2.add_pin(main.PC)
                    self.assertEqual(w2.visible_pins, {main.PC})
                    w2.restore_default_pins()
                    self.assertEqual(w2.visible_pins, {main.normalized(path) for path in original})
                finally:
                    w2.close()
                    w2.pool.waitForDone(5000)
            finally:
                w.close()
                w.pool.waitForDone(5000)

    def test_startup_location_and_geometry(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            QSettings.setDefaultFormat(QSettings.IniFormat)
            QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(d / "settings"))
            w = main.Explorer()
            w.show()
            try:
                w.navigate(str(d))
                wait_for(lambda: not w.workers)
                w.resize(900, 560)
                w.move(35, 45)
                app.processEvents()
                geometry = w.saveGeometry()
                splitter = w.splitter.saveState()
                w.close()
                self.assertEqual(w.settings.value("geometry"), geometry)
                self.assertEqual(w.settings.value("splitter"), splitter)
                self.assertEqual(main.normalized(w.settings.value("location")), main.normalized(str(d)))
                w2 = main.Explorer()
                w2.show()
                try:
                    wait_for(lambda: not w2.workers)
                    self.assertEqual(main.normalized(w2.current), main.normalized(str(d)))
                    self.assertEqual(w2.history, [w2.current])
                    w2.home_button.click()
                    wait_for(lambda: not w2.workers)
                    self.assertEqual(w2.current, main.PC)
                    self.assertEqual(w2.address.text(), "This PC")
                    self.assertEqual(len(w2.history), 2)
                    w2.back()
                    wait_for(lambda: not w2.workers)
                    self.assertEqual(main.normalized(w2.current), main.normalized(str(d)))
                    w2.go_home()
                    wait_for(lambda: not w2.workers)
                    self.assertEqual(w2.proxies[1].rowCount(), 0)
                    self.assertGreater(w2.proxies[0].rowCount(), 0)
                    # Qt may clamp geometry to the available offscreen display.
                    from PySide6.QtWidgets import QMainWindow
                    expected = QMainWindow()
                    expected.setMinimumSize(w2.minimumSize())
                    self.assertTrue(expected.restoreGeometry(geometry))
                    expected.show()
                    app.processEvents()
                    self.assertEqual(w2.size(), expected.size())
                    self.assertEqual(w2.pos(), expected.pos())
                    expected.close()
                finally:
                    w2.close()
                    w2.pool.waitForDone(5000)
                    app.processEvents()
                w.settings.setValue("location", str(d / "missing"))
                w.settings.sync()
                w3 = main.Explorer()
                try:
                    wait_for(lambda: not w3.workers)
                    self.assertEqual(w3.current, main.PC)
                finally:
                    w3.close()
                    w3.pool.waitForDone(5000)
                    app.processEvents()
            finally:
                w.close()
                w.pool.waitForDone(5000)
                app.processEvents()

    def test_views(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            QSettings.setDefaultFormat(QSettings.IniFormat)
            QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(d / "settings"))
            base = d / "sample"
            base.mkdir()
            for name in ["b.txt", "a.txt", "a.zip", "file.TAR.GZ", "test.7z", "regular.docx", ".hidden"]:
                (base / name).touch()
            (base / "folder.zip").mkdir()
            (base / "folder.zip" / "child.rar").touch()
            w = main.Explorer()
            w.show()
            try:
                w.navigate(str(base))
                wait_for(lambda: w.proxies[0].rowCount() == 4 and w.proxies[1].rowCount() == 3)
                names = lambda proxy: [proxy.index(i, 0).data() for i in range(proxy.rowCount())]
                self.assertEqual(names(w.proxies[0]), ["folder.zip", "a.txt", "b.txt", "regular.docx"])
                w.views[0].sortByColumn(0, Qt.DescendingOrder)
                self.assertEqual(names(w.proxies[0]), ["folder.zip", "regular.docx", "b.txt", "a.txt"])
                w.search.setText("a.")
                self.assertEqual(names(w.proxies[0]), ["a.txt"])
                self.assertEqual(names(w.proxies[1]), ["a.zip"])
                w.search.clear()
                w.toggle_hidden(True)
                self.assertEqual(w.proxies[0].rowCount(), 5)
                w.toggle_hidden(False)
                w.open_entry(w.views[0], w.proxies[0].index(0, 0))
                wait_for(lambda: w.proxies[1].rowCount() == 1 and w.proxies[0].rowCount() == 0)
                self.assertTrue(w.current.endswith("folder.zip"))
                w.back()
                wait_for(lambda: w.proxies[0].rowCount() == 4)
                w.forward()
                wait_for(lambda: w.proxies[1].rowCount() == 1)
                w.up()
                wait_for(lambda: w.proxies[0].rowCount() == 4)
                (base / "new.zip").touch()
                wait_for(lambda: w.proxies[1].rowCount() == 4)
                w.navigate(main.PC)
                wait_for(lambda: w.proxies[0].rowCount() > 0 and w.proxies[1].rowCount() == 0)
                self.assertEqual(w.tree.topLevelItem(0).text(0), "This PC")
                w.navigate(str(base))
                w.navigate(str(base / "folder.zip"))
                wait_for(lambda: w.proxies[1].rowCount() == 1)
                self.assertEqual(names(w.proxies[1]), ["child.rar"])
                wait_for(lambda: not w.workers)
                # Test larger folder scan, asynchronous model population, and filtering.
                big = d / "big"
                big.mkdir()
                for i in range(3000):
                    (big / f"file_{i:04d}.{'zip' if i % 5 == 0 else 'txt'}").touch()
                start = time.monotonic()
                w.navigate(str(big))
                wait_for(lambda: w.proxies[0].rowCount() == 2400 and w.proxies[1].rowCount() == 600)
                print(f"3000 entry load: {time.monotonic() - start:.3f} s")
                w.search.setText("file_000")
                self.assertEqual(w.proxies[0].rowCount(), 8)
                self.assertEqual(w.proxies[1].rowCount(), 2)
                wait_for(lambda: not w.workers)
            finally:
                w.close()
                w.pool.waitForDone(5000)
                app.processEvents()

if __name__ == "__main__":
    unittest.main(verbosity=2)
