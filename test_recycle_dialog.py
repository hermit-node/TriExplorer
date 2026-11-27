import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox
from services.recycle import share_recycle, share_trash_entries, purge_share_item
from recycle_dialog import RecycleDialog, discover_roots
from test_explorer import ExplorerTest, wait_for
from controllers.sidebar import RECYCLE_ROLE

app = QApplication.instance() or QApplication([])

class RecycleDialogTests(unittest.TestCase):
    def test_recycle_browser(self):
        class Host:
            current = ""
            pins = []
            def submit(self, fn, done):
                try:
                    result = fn()
                except Exception as exc:
                    done(None, str(exc))
                else:
                    done(result, None)
        from PySide6.QtWidgets import QWidget
        class Parent(QWidget, Host):
            def refresh(self):
                self.refreshed = True
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a = root / "a.txt"
            b = root / "b.txt"
            a.write_text("restore")
            b.write_text("purge")
            share_recycle(a, root)
            share_recycle(b, root)
            parent = Parent()
            with patch("recycle_dialog.discover_roots", return_value=[str(root)]), patch("recycle_dialog.native_entries", return_value=[]):
                d = RecycleDialog(parent)
                self.exercise_network(d, a, b, root, parent)

    def exercise_network(self, d, a, b, root, parent):
        self.assertEqual(d.table.topLevelItemCount(), 2)
        items = {d.table.topLevelItem(i).text(0): d.table.topLevelItem(i) for i in range(2)}
        items["a.txt"].setSelected(True)
        d.mutate(False)
        self.assertEqual(a.read_text(), "restore")
        self.assertEqual(d.table.topLevelItemCount(), 1)
        d.table.topLevelItem(0).setSelected(True)
        with patch.object(QMessageBox, "question", return_value=QMessageBox.No):
            d.mutate(True)
        self.assertEqual(len(share_trash_entries(root)), 1)
        with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
            d.mutate(True)
        self.assertEqual(share_trash_entries(root), [])
        self.assertFalse(b.exists())
        self.assertEqual(d.table.topLevelItemCount(), 0)
        d.reject()
        self.assertTrue(parent.refreshed)

    def test_hybrid_mixed_selection_routes_to_each_store(self):
        from PySide6.QtWidgets import QWidget
        class Parent(QWidget):
            current = ""
            pins = []
            def submit(self, fn, done):
                done(fn(), None)
            def refresh(self):
                pass
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            f = root / "network.txt"
            f.write_text("fixture")
            share_recycle(f, root)
            native = {"key": "shell-fixture", "name": "native.txt",
                      "original": "C:/fixture", "deleted": "2026-11-27"}
            parent = Parent()
            with patch("recycle_dialog.discover_roots", return_value=[str(root)]), patch("recycle_dialog.native_entries", return_value=[native]), patch("recycle_dialog.restore_native") as restore:
                dialog = RecycleDialog(parent)
                self.assertEqual(dialog.table.topLevelItemCount(), 2)
                dialog.filter.setCurrentIndex(1)
                self.assertEqual(dialog.table.topLevelItemCount(), 1)
                dialog.filter.setCurrentIndex(2)
                self.assertEqual(dialog.table.topLevelItemCount(), 1)
                dialog.filter.setCurrentIndex(0)
                dialog.table.selectAll()
                dialog.mutate(False)
                restore.assert_called_once_with("shell-fixture")
                self.assertEqual(f.read_text(), "fixture")
                dialog.reject()

    def test_reject_unexpected_entry_contents(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            file = root / "x.txt"
            file.write_text("data")
            share_recycle(file, root)
            key = share_trash_entries(root)[0][2]
            from services.recycle import trash_directory
            entry = trash_directory(root) / key
            (entry / "unexpected.txt").write_text("keep")
            with self.assertRaises(ValueError):
                purge_share_item(root, key)
            self.assertEqual((entry / "payload").read_text(), "data")
            self.assertEqual((entry / "unexpected.txt").read_text(), "keep")
