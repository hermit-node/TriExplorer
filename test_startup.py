import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from explorer import Explorer
from test_explorer import wait_for

app=QApplication.instance() or QApplication([])

class StartupTests(unittest.TestCase):
    def test_slow_saved_location_does_not_block_window_or_override_navigation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            first=root/"saved"
            second=root/"manual"
            first.mkdir()
            second.mkdir()
            settings=QSettings(str(root/"settings.ini"),QSettings.IniFormat)
            settings.setValue("location",str(first))
            settings.setValue("cache/sqlite_path",str(root/"cache.sqlite3"))
            release=threading.Event()
            started=threading.Event()
            def slow(path):
                started.set()
                return release.wait(5)
            with patch("explorer.initial_folder_exists",side_effect=slow):
                w=Explorer(settings)
                try:
                    w.show()
                    wait_for(started.is_set)
                    self.assertTrue(w.isVisible())
                    self.assertIsNone(w.current)
                    w.navigate(str(second))
                    release.set()
                    wait_for(lambda: not w.workers)
                    self.assertEqual(w.current,str(second))
                finally:
                    release.set()
                    w.close()
                    w.pool.waitForDone(5000)
                    app.processEvents()
