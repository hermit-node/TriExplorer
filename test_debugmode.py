"""Exercise debug-mode isolation and real Qt watcher delivery."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import tempfile
import unittest
import time
from pathlib import Path
from PySide6.QtWidgets import QApplication
from debugmode import DebugExplorer, isolated_settings
from file_types import normalized

app = QApplication.instance() or QApplication([])

def wait_for(predicate, seconds=8):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        if predicate():
            return
        time.sleep(.015)
    raise AssertionError("Timed out")

class DebugModeTests(unittest.TestCase):
    def test_watcher_logs_without_refresh_and_manual_refresh_works(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "files"
            folder.mkdir()
            (folder / "first.txt").write_text("1")
            source = root / "normal.ini"
            original = "[watcher]\nenabled=true\nnetwork_enabled=true\n"
            source.write_text(original)
            session = root / "session"
            session.mkdir()
            settings = isolated_settings(session, source, str(folder))
            w = DebugExplorer(settings)
            try:
                wait_for(lambda: not w.workers)
                self.assertEqual(normalized(w.current), normalized(str(folder)))
                self.assertFalse(w.watcher_enabled)
                self.assertTrue(w.should_watch_folder(True))
                self.assertEqual(w.model.rowCount(), 1)
                generation = w.generation
                with self.assertLogs("triexplorer.debug", "INFO") as logs:
                    (folder / "second.txt").write_text("2")
                    wait_for(lambda: any("WATCH notification" in s for s in logs.output))
                    self.assertEqual(w.generation, generation)
                    self.assertFalse(w.refresh_timer.isActive())
                    self.assertEqual(w.model.rowCount(), 1)
                    w.refresh()
                    wait_for(lambda: not w.workers)
                    self.assertEqual(w.model.rowCount(), 2)
                self.assertTrue(any("LOAD request" in s for s in logs.output))
                self.assertTrue(any("SCAN entries=2" in s for s in logs.output))
            finally:
                w.close()
                w.pool.waitForDone(5000)
                app.processEvents()
            self.assertEqual(source.read_text(), original)
