"""Table panes, source rows, filters, inline rename, and lazy image preview.

Filters and file categories are defined only in filters.py.
This module intentionally has no dependency on the Explorer window/controller.
"""
from __future__ import annotations

import os
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime

from PySide6.QtCore import (
    Qt, QModelIndex, QAbstractTableModel, QSortFilterProxyModel,
    QUrl, QMimeData, QEvent, QSize, Signal
)
from PySide6.QtGui import QDrag, QImageReader, QPixmap
from PySide6.QtWidgets import (
    QTreeView, QAbstractItemView, QLineEdit, QLabel,
    QComboBox, QWidget, QFrame, QVBoxLayout, QHBoxLayout,
    QHeaderView, QSizePolicy, QCheckBox
)
from filters import PANE_MODES, CATEGORY_SUFFIXES
from file_types import PC, Entry, normalized, readable_size

PATH_ROLE = Qt.ItemDataRole.UserRole
SORT_ROLE = int(PATH_ROLE) + 1
LOADED_ROLE = int(PATH_ROLE) + 2







class Files(QAbstractTableModel):
    headers = ("Name", "Size", "Type", "Modified")

    def __init__(self, icons, parent=None):
        super().__init__(parent)
        self.entries = []
        self.icons = icons
        self.folder_sizes = {}
        self.archive_notes = {}

    def replace(self, entries, sizes=None):
        self.beginResetModel()
        self.entries = entries
        self.folder_sizes = sizes or {}
        self.endResetModel()

    def reconcile(self, entries, sizes):
        """Apply a same-directory snapshot without resetting persistent indexes."""
        incoming = {normalized(e.path): e for e in entries}
        removed = [i for i, e in enumerate(self.entries) if normalized(e.path) not in incoming]
        # Remove contiguous runs backwards, retaining Qt indexes of unaffected rows.
        while removed:
            end = start = removed.pop()
            while removed and removed[-1] == start - 1:
                start = removed.pop()
            self.beginRemoveRows(QModelIndex(), start, end)
            del self.entries[start:end + 1]
            self.endRemoveRows()
        old_sizes = self.folder_sizes
        new_sizes = {k: v for k, v in old_sizes.items() if k in incoming and incoming[k].folder}
        # Preserve fresher results delivered by an ongoing size worker.
        for k, v in sizes.items():
            if k not in new_sizes or v.checked >= new_sizes[k].checked:
                new_sizes[k] = v
        for k, v in list(new_sizes.items()):
            e = incoming[k]
            if e.modified is not None and abs(e.modified - v.modified) > 0.00001 and not v.stale:
                from dataclasses import replace
                new_sizes[k] = replace(v, stale=True)
        self.folder_sizes = new_sizes
        changed = []
        for i, old in enumerate(self.entries):
            k = normalized(old.path)
            new = incoming.pop(k)
            if old != new or old_sizes.get(k) != new_sizes.get(k):
                self.entries[i] = new
                changed.append(i)
        # Coalesce dataChanged ranges instead of sorting for every metadata update.
        while changed:
            start = end = changed.pop()
            while changed and changed[-1] == start - 1:
                start = changed.pop()
            self.dataChanged.emit(self.index(start, 0), self.index(end, 3), [])
        if incoming:
            start = len(self.entries)
            self.beginInsertRows(QModelIndex(), start, start + len(incoming) - 1)
            self.entries.extend(incoming.values())
            self.endInsertRows()

    def set_folder_size(self, path, size):
        self.folder_sizes[path] = size
        for row, entry in enumerate(self.entries):
            if entry.folder and normalized(entry.path) == path:
                index = self.index(row, 1)
                self.dataChanged.emit(index, index, [Qt.DisplayRole, Qt.ToolTipRole, SORT_ROLE])
                break

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.entries)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else 4

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self.headers[section]

    def entry(self, index):
        return self.entries[index.row()] if index.isValid() else None

    def data(self, index, role=Qt.DisplayRole):
        e = self.entry(index)
        if e is None:
            return None
        col = index.column()
        cached = self.folder_sizes.get(normalized(e.path)) if e.folder else None
        size = cached.size if cached is not None else e.size
        kind = "Folder" if e.folder else ("Archive" if e.archive else (
                Path(e.name).suffix[1:].upper() or "File"))
        if role == PATH_ROLE:
            return e.path
        if role == SORT_ROLE:
            return (e.name.casefold(), size if size is not None else -1,
                    kind.casefold(), e.modified if e.modified is not None else -1)[col]
        if role == Qt.DecorationRole and col == 0:
            return self.icons["folder" if e.folder else "archive" if e.archive else "file"]
        if role == Qt.ToolTipRole:
            if e.folder and col == 1:
                if cached is None:
                    return "Folder size not calculated"
                stamp = datetime.fromtimestamp(cached.checked).strftime("%Y-%m-%d %H:%M")
                return f"{cached.size:,} bytes (logical total)\nCalculated: {stamp}" + (
                    "\nCached value may be stale" if cached.stale else "")
            note = self.archive_notes.get(normalized(e.path))
            return e.path + ("\n" + note if note else "")
        if role == Qt.TextAlignmentRole and col == 1:
            return int(Qt.AlignRight | Qt.AlignVCenter)
        if role == Qt.DisplayRole:
            date = datetime.fromtimestamp(e.modified).strftime("%Y-%m-%d  %H:%M") if e.modified is not None else ""
            return (e.name, ("~" if cached and cached.stale else "") + readable_size(size), kind, date)[col]

    def flags(self, index):
        return super().flags(index) | (Qt.ItemIsDragEnabled if index.isValid() else Qt.NoItemFlags)

    def mimeTypes(self):
        return ["text/uri-list"]

    def mimeData(self, indexes):
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(self.entries[r].path)
                      for r in sorted({i.row() for i in indexes})])
        return mime

    def supportedDragActions(self):
        return Qt.CopyAction | Qt.MoveAction


class PaneFilter(QSortFilterProxyModel):
    def __init__(self, mode, parent=None):
        super().__init__(parent)
        self.mode = ("archives" if mode else "default") if isinstance(mode, bool) else mode
        self.query = ""
        self.duplicate_paths = set()
        self.archive_match_paths = set()
        self.hidden = False
        self.setDynamicSortFilter(True)
        self.setSortRole(SORT_ROLE)

    @property
    def archives(self):
        return self.mode == "archives"

    def set_mode(self, mode):
        if mode not in dict(PANE_MODES):
            mode = "default"
        if self.mode != mode:
            self.mode = mode
            self.invalidate()

    def filterAcceptsRow(self, row, parent):
        e = self.sourceModel().entries[row]
        if (not self.hidden and e.hidden) or self.query not in e.name.casefold():
            return False
        mode = self.mode
        if mode in ("all", "alphabetical"):
            return True
        if mode == "duplicates":
            return not e.folder and normalized(e.path) in self.duplicate_paths
        if mode == "archive_matches":
            return normalized(e.path) in self.archive_match_paths
        if mode == "default":
            return not e.archive
        if mode == "folders":
            return e.folder
        if mode == "files":
            return not e.folder
        if mode == "archives":
            return e.archive
        return not e.folder and not e.archive and os.path.splitext(e.name.casefold())[1] in CATEGORY_SUFFIXES.get(mode,
                                                                                                                  ())

    def lessThan(self, left, right):
        a = self.sourceModel().entry(left)
        b = self.sourceModel().entry(right)
        if self.mode not in ("alphabetical", "archive_matches") and a.folder != b.folder:
            return a.folder if self.sortOrder() == Qt.AscendingOrder else b.folder
        return super().lessThan(left, right)


class RenameEdit(QLineEdit):
    commit_name = Signal()
    cancel_name = Signal()

    def event(self, event):
        if event.type() == QEvent.ShortcutOverride and event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Escape):
            event.accept()
            return True
        return super().event(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.commit_name.emit()
            event.accept()
        elif event.key() == Qt.Key_Escape:
            self.cancel_name.emit()
            event.accept()
        else:
            super().keyPressEvent(event)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.cancel_name.emit()


def read_image_preview(path, cancelled):
    if cancelled():
        return None
    reader = QImageReader(path)
    reader.setAutoTransform(True)
    original = reader.size()
    if not original.isValid():
        raise ValueError(reader.errorString() or "Unsupported image format")
    # Some plugins must decode the whole image before scaling. Bound both input
    # dimensions and the retained image; never use a full-resolution pixmap.
    if original.width() * original.height() > 80_000_000:
        raise ValueError("Image too large for preview (80 megapixel limit)")
    scaled = original.scaled(QSize(1400, 1400), Qt.KeepAspectRatio)
    if original.width() > 1400 or original.height() > 1400:
        reader.setScaledSize(scaled)
    if cancelled():
        return None
    image = reader.read()
    if cancelled():
        return None
    if image.isNull():
        raise ValueError(reader.errorString() or "Unable to decode image")
    if image.width() > 1400 or image.height() > 1400:
        image = image.scaled(QSize(1400, 1400), Qt.KeepAspectRatio, Qt.SmoothTransformation)
    return image, original


class PreviewLabel(QLabel):
    def __init__(self):
        super().__init__("Select an image")
        self.setAlignment(Qt.AlignCenter)
        self.setWordWrap(True)
        self.setMinimumSize(0, 0)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.source = None

    def reset(self, message):
        self.source = None
        self.clear()
        self.setText(message)

    def set_image(self, image):
        self.source = QPixmap.fromImage(image)
        self.fit()

    def fit(self):
        if self.source is not None and not self.source.isNull():
            size = self.contentsRect().size()
            if size.width() > 0 and size.height() > 0:
                self.setPixmap(self.source.scaled(size, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit()


class FileView(QTreeView):
    dropped = Signal(list, str, bool)
    archive_dropped = Signal(list, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setRootIsDecorated(False)
        self.setItemsExpandable(False)
        self.setUniformRowHeights(True)
        self.setAlternatingRowColors(True)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setDropIndicatorShown(False)
        self.setSortingEnabled(True)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setTextElideMode(Qt.ElideMiddle)

    def startDrag(self, supported):
        indexes = self.selectionModel().selectedRows()
        if indexes and all(os.path.dirname(i.data(PATH_ROLE)) != i.data(PATH_ROLE) for i in indexes):
            drag = QDrag(self)
            drag.setMimeData(self.model().mimeData(indexes))
            drag.exec(Qt.CopyAction | Qt.MoveAction, Qt.CopyAction)

    def cross_pane_entries(self, event):
        source = event.source()
        if not isinstance(source, FileView) or source is self:
            return []
        paths = {normalized(u.toLocalFile()) for u in event.mimeData().urls() if u.isLocalFile()}
        if not paths:
            return []
        selected = [source.model().sourceModel().entry(source.model().mapToSource(i))
                    for i in source.selectionModel().selectedRows()]
        if {normalized(e.path) for e in selected if e} == paths:
            return [e for e in selected if e]
        matched = [e for e in source.model().sourceModel().entries if normalized(e.path) in paths]
        return matched if len(matched) == len(paths) else []

    def is_archive_drop(self, event):
        entries = self.cross_pane_entries(event)
        return bool(entries and all(e.archive for e in entries))

    def dragEnterEvent(self, event):
        entries = self.cross_pane_entries(event)
        if any(e.archive for e in entries) and not all(e.archive for e in entries):
            event.ignore()
            return
        if entries and all(e.archive for e in entries) and event.mimeData().hasUrls():
            event.setDropAction(Qt.CopyAction)
            event.accept()
        elif event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    dragMoveEvent = dragEnterEvent

    def dropEvent(self, event):
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if not paths:
            event.ignore()
            return
        proxy = self.model()
        index = self.indexAt(event.position().toPoint())
        entry = proxy.sourceModel().entry(proxy.mapToSource(index))
        target = entry.path if entry and entry.folder else ""
        move = bool(event.modifiers() & Qt.ShiftModifier) or event.proposedAction() == Qt.MoveAction
        entries = self.cross_pane_entries(event)
        if entries and all(e.archive for e in entries):
            self.archive_dropped.emit(paths, target)
        elif any(e.archive for e in entries):
            # Never turn a mixed ZIP/file selection into a move by accident.
            event.ignore()
            return
        else:
            self.dropped.emit(paths, target, move)
        # Never signal an external drag source to delete before our worker finishes.
        event.setDropAction(Qt.CopyAction)
        event.accept()


@dataclass(frozen=True)
class TablePane:
    """One independently configured table in a shared-directory explorer."""
    widget: QWidget
    view: FileView
    proxy: PaneFilter
    count: QLabel
    selector: QComboBox
    containment: QCheckBox
    footer: QWidget


def create_table_pane(index, default_mode, settings, source_model, show_hidden, parent=None):
    """Build a pane without wiring it to application-specific Explorer actions."""
    pane = QFrame(objectName="pane")
    layout = QVBoxLayout(pane)
    layout.setContentsMargins(1, 1, 1, 1)
    layout.setSpacing(3)

    count = QLabel("0", objectName="muted")
    mode = settings.value(f"pane/{index}/mode", default_mode, type=str)
    if mode not in dict(PANE_MODES):
        mode = default_mode

    selector = QComboBox()
    selector.setToolTip("Choose what this pane displays. Both panes share the current directory.")
    for key, label in PANE_MODES:
        selector.addItem(label, key)
    selector.setCurrentIndex(selector.findData(mode))
    heading = QWidget()
    bar = QHBoxLayout(heading)
    bar.setContentsMargins(7, 5, 7, 4)
    bar.addWidget(selector, 1)
    bar.addWidget(count)
    layout.addWidget(heading)

    view = FileView()
    proxy = PaneFilter(mode, parent)
    proxy.hidden = show_hidden
    proxy.setSourceModel(source_model)
    view.setModel(proxy)

    column = settings.value(f"pane/{index}/sort_column", 0, type=int)
    descending = settings.value(f"pane/{index}/descending", False, type=bool)
    view.sortByColumn(column if 0 <= column < source_model.columnCount() else 0,
                      Qt.DescendingOrder if descending else Qt.AscendingOrder)
    header = view.header()
    header.setSectionsMovable(True)
    header.setStretchLastSection(False)
    header.setSectionResizeMode(0, QHeaderView.Stretch)
    header.resizeSection(1, 82)
    header.resizeSection(2, 78)
    header.resizeSection(3, 145)
    view.setToolTip(
        "Drag ZIP files to the opposite pane to extract and recycle the ZIP. "
        "Drop on a folder to extract inside it."
    )
    layout.addWidget(view, 1)
    footer = QWidget()
    bottom = QHBoxLayout(footer)
    bottom.setContentsMargins(7, 2, 7, 4)
    bottom.addStretch()
    containment = QCheckBox("Include contained archives")
    containment.setToolTip("Include ZIP/ISO files fully represented by folders that contain additional files")
    bottom.addWidget(containment)
    layout.addWidget(footer)
    footer.setVisible(mode == "archive_matches")
    return TablePane(pane, view, proxy, count, selector, containment, footer)
