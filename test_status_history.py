import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QApplication
from explorer import Explorer
from status_history import StatusHistory
from test_explorer import wait_for

app = QApplication.instance() or QApplication([])


class StatusHistoryTests(unittest.TestCase):
    def test_collapsed_history_captures_and_reopens(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = QSettings(str(root / "settings.ini"), QSettings.IniFormat)
            settings.setValue("cache/sqlite_path", str(root / "cache.sqlite3"))
            w = Explorer(settings)
            try:
                w.show()
                wait_for(lambda: not w.workers)
                self.assertTrue(w.status_history.isHidden())
                w.statusBar().showMessage("Extracting first.zip...")
                w.statusBar().showMessage("Extracting second.zip...")
                w.statusBar().clearMessage()
                w.history_button.click()
                app.processEvents()
                self.assertTrue(w.status_history.isVisible())
                text = w.status_history.output.toPlainText()
                self.assertIn("Extracting first.zip...", text)
                self.assertIn("Extracting second.zip...", text)
                self.assertLess(text.index("first.zip"), text.index("second.zip"))
                self.assertGreater(w.content_splitter.sizes()[1], 0)
                w.history_button.click()
                self.assertTrue(w.status_history.isHidden())
                w.statusBar().showMessage("Finished")
                w.history_button.click()
                self.assertIn("Finished", w.status_history.output.toPlainText())
                w.status_history.copy_button.click()
                self.assertEqual(app.clipboard().text(), w.status_history.output.toPlainText())
                w.status_history.clear_button.click()
                self.assertEqual(w.status_history.output.toPlainText(), "")
                self.assertEqual(w.statusBar().currentMessage(), "Finished")
            finally:
                w.close()
                w.pool.waitForDone(5000)
                app.processEvents()

    def test_history_bounded_and_selection_preserved(self):
        history = StatusHistory()
        history.resize(500, 200)
        history.show()
        for i in range(50):
            history.record(f"message {i}")
        app.processEvents()
        cursor = history.output.textCursor()
        cursor.movePosition(QTextCursor.Start)
        cursor.movePosition(QTextCursor.EndOfBlock, QTextCursor.KeepAnchor)
        history.output.setTextCursor(cursor)
        history.output.verticalScrollBar().setValue(0)
        selected = cursor.selectedText()
        history.record("new message")
        self.assertEqual(history.output.textCursor().selectedText(), selected)
        self.assertEqual(history.output.verticalScrollBar().value(), 0)
        for i in range(600):
            history.record(f"event {i}")
        self.assertEqual(history.output.document().blockCount(), history.MAX_LINES)
        self.assertIn("event 599", history.output.toPlainText())
        self.assertNotIn("message 0", history.output.toPlainText())
        history.close()
