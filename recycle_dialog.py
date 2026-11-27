"""Unified local Windows and network recycle browser."""
from __future__ import annotations
import os
from pathlib import Path
from PySide6.QtCore import QDir, Qt
from PySide6.QtGui import QColor, QBrush
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QTreeWidget,
    QTreeWidgetItem, QPushButton, QAbstractItemView, QMessageBox, QHeaderView
)
from file_types import normalized
from services.recycle import network_root, share_trash_entries, restore_share_item, purge_share_item
from services.native_recycle import native_entries, restore_native, purge_native


def discover_roots(current, pins):
    candidates = [current, *pins, *(d.absoluteFilePath() for d in QDir.drives())]
    roots = {}
    for path in candidates:
        if path:
            root = network_root(path)
            if root is not None:
                roots.setdefault(normalized(str(root)), str(root))
    return list(roots.values())


def collect_records(current, pins):
    records, errors = [], []
    if os.name == "nt":
        try:
            for row in native_entries():
                records.append(("Windows", "", row["key"], row["name"],
                                row["original"], row["deleted"]))
        except Exception as exc:
            errors.append("Windows: " + str(exc))
    try:
        roots = discover_roots(current, pins)
    except Exception as exc:
        roots = []
        errors.append("Network discovery: " + str(exc))
    for root in roots:
        try:
            for date, relative, key in share_trash_entries(root):
                records.append(("Network", root, key, relative.rsplit("/",1)[-1],
                                str((Path(root) / relative).parent), date))
        except Exception as exc:
            errors.append(root + ": " + str(exc))
    return records, errors


class RecycleDialog(QDialog):
    def __init__(self, explorer):
        super().__init__(explorer)
        self.explorer = explorer
        self.closed = False
        self.busy = False
        self.changed = False
        self.records = []
        self.setWindowTitle("Recycle Bin")
        self.resize(1000, 550)
        self.finished.connect(self.on_closed)
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel("Show:"))
        self.filter = QComboBox()
        self.filter.addItems(["All recycled items", "Windows", "Network"])
        row.addWidget(self.filter, 1)
        self.reload = QPushButton("Refresh")
        row.addWidget(self.reload)
        layout.addLayout(row)
        self.table = QTreeWidget()
        self.table.setHeaderLabels(["Name", "Original location", "Recycled", "Source"])
        self.table.setRootIsDecorated(False)
        self.table.setUniformRowHeights(True)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.header().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setColumnWidth(0, 220)
        self.table.setColumnWidth(2, 170)
        self.table.setColumnWidth(3, 140)
        self.table.setSortingEnabled(True)
        self.table.sortItems(2, Qt.DescendingOrder)
        layout.addWidget(self.table)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        self.restore = QPushButton("Restore selected")
        self.purge = QPushButton("Delete permanently...")
        self.close_button = QPushButton("Close")
        for b in (self.restore, self.purge):
            buttons.addWidget(b)
        buttons.addStretch()
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)
        self.filter.currentIndexChanged.connect(self.render)
        self.reload.clicked.connect(self.load)
        self.restore.clicked.connect(lambda: self.mutate(False))
        self.purge.clicked.connect(lambda: self.mutate(True))
        self.close_button.clicked.connect(self.reject)
        self.table.itemSelectionChanged.connect(self.update_buttons)
        self.load()

    def on_closed(self, *_):
        self.closed = True
        if self.changed:
            self.explorer.refresh()

    def set_busy(self, busy):
        self.busy = busy
        self.filter.setEnabled(not busy)
        self.reload.setEnabled(not busy)
        self.update_buttons()

    def update_buttons(self):
        enabled = not self.busy and bool(self.table.selectedItems())
        self.restore.setEnabled(enabled)
        self.purge.setEnabled(enabled)

    def render(self, *_):
        self.table.clear()
        selected = self.filter.currentIndex()
        self.table.setSortingEnabled(False)
        for index, (kind, root, key, name, location, date) in enumerate(self.records):
            if selected and selected != (1 if kind == "Windows" else 2):
                continue
            source = "Windows" if kind == "Windows" else "Network: " + root
            item = QTreeWidgetItem([name, location, date, source])
            item.setData(0, Qt.UserRole, index)
            item.setToolTip(3, source)
            # Source label is also explicit, so color is not the only distinction.
            item.setBackground(3, QBrush(QColor("#234c78" if kind == "Windows" else "#276148")))
            item.setForeground(3, QBrush(QColor("#ffffff")))
            self.table.addTopLevelItem(item)
        self.table.setSortingEnabled(True)

    def load(self, *_):
        if self.closed or self.busy:
            return
        self.status.setText("Loading Windows and network recycle records...")
        self.set_busy(True)
        current, pins = self.explorer.current, list(self.explorer.pins)
        def done(result, error):
            if self.closed:
                return
            self.records, errors = result if result is not None else ([], [error])
            self.render()
            self.set_busy(False)
            windows = sum(row[0] == "Windows" for row in self.records)
            self.status.setText(f"{len(self.records)} item(s): {windows} Windows, "
                                f"{len(self.records)-windows} network" +
                                (" | Some sources unavailable: " + "; ".join(errors) if errors else ""))
        self.explorer.submit(lambda: collect_records(current, pins), done)

    def mutate(self, permanent):
        selected = self.table.selectedItems()
        if self.busy or not selected:
            return
        rows = [self.records[item.data(0, Qt.UserRole)] for item in selected]
        if permanent:
            answer = QMessageBox.question(self, "Permanently delete?",
                f"Permanently delete {len(rows)} selected item(s)?\nThis cannot be undone.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                return
        self.set_busy(True)
        self.close_button.setEnabled(False)
        self.status.setText("Deleting..." if permanent else "Restoring...")
        def work():
            errors, count = [], 0
            for kind, root, key, name, location, date in rows:
                try:
                    if kind == "Windows":
                        (purge_native if permanent else restore_native)(key)
                    else:
                        (purge_share_item if permanent else restore_share_item)(root, key)
                    count += 1
                except Exception as exc:
                    errors.append(name + ": " + str(exc))
            return count, errors
        def done(result, error):
            if self.closed:
                return
            count, errors = result if result is not None else (0, [error])
            self.changed |= bool(count)
            self.set_busy(False)
            self.close_button.setEnabled(True)
            if errors:
                QMessageBox.warning(self, "Recycle Bin", "\n".join(errors))
            self.load()
        self.explorer.submit(work, done)

    def reject(self):
        if self.close_button.isEnabled():
            super().reject()

    def closeEvent(self, event):
        if not self.close_button.isEnabled():
            event.ignore()
        else:
            super().closeEvent(event)
