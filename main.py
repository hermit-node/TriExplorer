"""TriExplorer: synchronized file and archive browser."""
from __future__ import annotations

import os
import json
import sqlite3
import uuid
import shutil
import sys
import tempfile
import zipfile
from threading import Event
import duplicates
import archive_matches
import folder_sizes
from collections import deque
from dataclasses import dataclass, replace
from pathlib import Path
from datetime import datetime

from PySide6.QtCore import (Qt, QAbstractTableModel, QModelIndex, QSortFilterProxyModel,
    QDir, QUrl, QMimeData, QFile, QFileSystemWatcher, QTimer, QSettings,
    QObject, Signal, QRunnable, QThreadPool, QStandardPaths, QEvent, QSize, QPropertyAnimation, QEasingCurve)
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence, QDrag, QPalette, QColor, QIcon, QPixmap, QPainter, QImageReader
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
    QHBoxLayout, QSplitter, QTreeWidget, QTreeWidgetItem, QTreeView, QHeaderView,
    QAbstractItemView, QToolButton, QLineEdit, QLabel, QMenu, QMessageBox,
    QInputDialog, QStyle, QFrame, QFileDialog, QComboBox, QSizePolicy)

APP = "TriExplorer"
PC = ""
ARCHIVE_SUFFIXES = (".zip", ".7z", ".rar", ".tar", ".tgz", ".tbz", ".tbz2",
    ".txz", ".gz", ".bz2", ".xz", ".zst", ".cab", ".arj", ".lz", ".lzma",
    ".iso", ".wim", ".esd", ".zipx", ".z", ".lzh", ".cpio", ".001", ".tar.zst")
PATH_ROLE = Qt.ItemDataRole.UserRole
SORT_ROLE = int(PATH_ROLE) + 1
LOADED_ROLE = int(PATH_ROLE) + 2

def normalized(path):
    return os.path.normcase(os.path.normpath(path)) if path else PC

def archive_name(name):
    return name.lower().endswith(ARCHIVE_SUFFIXES)

def readable_size(n):
    if n is None:
        return ""
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if value < 1024 or unit == "PB":
            return f"{int(value)} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024

@dataclass(frozen=True)
class Entry:
    path: str
    name: str
    folder: bool
    archive: bool = False
    size: int | None = None
    modified: float | None = None
    hidden: bool = False

def scan(path):
    if path == PC:
        return [Entry(os.path.normpath(d.absoluteFilePath()), d.absoluteFilePath(), True)
                for d in QDir.drives()]
    entries = []
    with os.scandir(path) as it:
        for item in it:
            if item.name.casefold() == NETWORK_TRASH.casefold():
                continue
            try:
                folder = item.is_dir()
                stat = item.stat(follow_symlinks=False)
                hidden = item.name.startswith(".") or bool(getattr(stat, "st_file_attributes", 0) & 2)
                entries.append(Entry(item.path, item.name, folder,
                    not folder and archive_name(item.name),
                    None if folder else stat.st_size, stat.st_mtime, hidden))
            except OSError:
                # A file may disappear or deny metadata access during enumeration.
                try:
                    folder = item.is_dir()
                except OSError:
                    folder = False
                entries.append(Entry(item.path, item.name, folder,
                    not folder and archive_name(item.name), hidden=item.name.startswith(".")))
    return entries

class WorkerSignals(QObject):
    finished = Signal(object, object)
    progress = Signal(object, object, object)

class Worker(QRunnable):
    def __init__(self, fn):
        super().__init__()
        self.fn = fn
        self.signals = WorkerSignals()

    def run(self):
        try:
            result = self.fn()
        except Exception as exc:
            self.signals.finished.emit(None, str(exc))
        else:
            self.signals.finished.emit(result, None)

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
                return f"{cached.size:,} bytes (logical total)\nCalculated: {stamp}" + ("\nCached value may be stale" if cached.stale else "")
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

PANE_MODES = (
    ("default", "Files & folders"),
    ("all", "All items"),
    ("alphabetical", "All items · A–Z"),
    ("folders", "Folders only"),
    ("files", "Files only"),
    ("archives", "Compressed archives"),
    ("images", "Images"),
    ("documents", "Documents"),
    ("video", "Videos"),
    ("audio", "Audio"),
    ("code", "Code & scripts"),
    ("duplicates", "Duplicates only (SHA-256)"),
    ("archive_matches", "ZIP / folder matches"),
)
CATEGORY_SUFFIXES = {
    "images": frozenset(".jpg .jpeg .png .gif .webp .bmp .tiff .tif .svg .ico .heic .heif .avif .jxl .raw .dng .psd .ai .cr2 .cr3 .nef .arw .orf .rw2".split()),
    "documents": frozenset(".pdf .doc .docx .odt .rtf .txt .md .xls .xlsx .ods .csv .tsv .ppt .pptx .odp .epub .mobi".split()),
    "video": frozenset(".mp4 .mkv .avi .mov .wmv .webm .m4v .mpeg .mpg .mts .m2ts .flv .3gp".split()),
    "audio": frozenset(".mp3 .wav .flac .aac .m4a .ogg .opus .wma .aiff .aif .alac .mid .midi".split()),
    "code": frozenset(".py .pyw .js .jsx .ts .tsx .mjs .cjs .html .css .scss .json .yaml .yml .toml .xml .sql .sh .bat .cmd .ps1 .c .h .cpp .hpp .cs .java .kt .go .rs .rb .php .swift .lua .r .vue .svelte .ipynb".split()),
}

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
        return not e.folder and not e.archive and os.path.splitext(e.name.casefold())[1] in CATEGORY_SUFFIXES.get(mode, ())

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
    if original.width()*original.height() > 80_000_000:
        raise ValueError("Image too large for preview (80 megapixel limit)")
    scaled = original.scaled(QSize(1400,1400), Qt.KeepAspectRatio)
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
        image = image.scaled(QSize(1400,1400), Qt.KeepAspectRatio, Qt.SmoothTransformation)
    return image, original


class PreviewLabel(QLabel):
    def __init__(self):
        super().__init__("Select an image")
        self.setAlignment(Qt.AlignCenter)
        self.setWordWrap(True)
        self.setMinimumSize(0,0)
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

def valid_name(name):
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                *(f"LPT{i}" for i in range(1, 10))}
    if os.name == "nt" and name.split(".")[0].strip().upper() in reserved:
        return False
    return bool(name and name not in (".", "..") and not any(c in name for c in '/\\<>:"|?*')
                and not name.endswith((" ", ".")) and not any(ord(c) < 32 for c in name))

def reject_junctions(path):
    if getattr(os.path, "isjunction", lambda _: False)(path):
        raise ValueError("Copying a junction is not supported")
    if os.path.isdir(path) and not os.path.islink(path):
        for base, dirs, _files in os.walk(path, followlinks=False):
            for name in dirs:
                if getattr(os.path, "isjunction", lambda _: False)(os.path.join(base, name)):
                    raise ValueError(f"Folder contains a junction: {os.path.join(base, name)}")

def transfer(paths, target, move):
    errors = []
    for source in paths:
        try:
            source = os.path.abspath(source)
            if os.path.dirname(source) == source:
                raise ValueError("Cannot transfer a drive root")
            dest = os.path.join(target, os.path.basename(source))
            if normalized(source) == normalized(dest):
                raise ValueError("Source and destination are the same")
            if os.path.lexists(dest):
                raise FileExistsError(f"Already exists: {dest}")
            if os.path.isdir(source):
                real_src, real_dest = os.path.realpath(source), os.path.realpath(dest)
                try:
                    child = normalized(os.path.commonpath([real_src, real_dest])) == normalized(real_src)
                except ValueError:
                    child = False
                if child:
                    raise ValueError("Cannot copy or move a folder inside itself")
            if move:
                # Windows rename does not replace an existing destination.
                if os.name == "nt":
                    try:
                        os.rename(source, dest)
                    except OSError as exc:
                        if getattr(exc, "winerror", None) != 17:
                            raise
                        if os.path.isdir(source):
                            reject_junctions(source)
                            shutil.copytree(source, dest, symlinks=True)
                            shutil.rmtree(source)
                        else:
                            with open(source, "rb") as src, open(dest, "xb") as dst:
                                shutil.copyfileobj(src, dst, 1024 * 1024)
                            shutil.copystat(source, dest)
                            os.unlink(source)
                else:
                    shutil.move(source, dest)
            elif os.path.isdir(source):
                reject_junctions(source)
                shutil.copytree(source, dest, symlinks=True)
            elif os.path.islink(source):
                os.symlink(os.readlink(source), dest)
            else:
                with open(source, "rb") as src, open(dest, "xb") as dst:
                    shutil.copyfileobj(src, dst, 1024 * 1024)
                shutil.copystat(source, dest)
        except Exception as exc:
            errors.append(f"{source}: {exc}")
    return errors

NETWORK_TRASH = ".TriExplorer-Recycle"

def network_root(path):
    if os.name != "nt":
        return None
    import ctypes
    drive, _ = os.path.splitdrive(os.path.abspath(path))
    if not drive:
        return None
    root = drive + os.sep
    if drive.startswith("\\\\") or ctypes.windll.kernel32.GetDriveTypeW(root) == 4:
        return Path(root)
    return None

def trash_directory(root):
    return Path(root) / NETWORK_TRASH

def checked_trash(root):
    trash = trash_directory(root)
    if trash.is_symlink() or getattr(trash, "is_junction", lambda: False)():
        raise ValueError("Recycle directory cannot be a link or junction")
    return trash

def share_recycle(path, root):
    root = Path(root)
    source = Path(os.path.abspath(path))
    relative = source.relative_to(root)
    if not relative.parts or relative.parts[0].casefold() == NETWORK_TRASH.casefold():
        raise ValueError("Cannot recycle the share root or its recycle directory")
    trash = checked_trash(root)
    trash.mkdir(exist_ok=True)
    # One uniquely named container per deletion; preserve the exact original name.
    entry = trash / uuid.uuid4().hex
    entry.mkdir()
    try:
        metadata = {"original": relative.as_posix(), "deleted": datetime.now().isoformat(timespec="seconds")}
        (entry / "restore.json").write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
        os.rename(source, entry / "payload")
    except Exception:
        if not os.path.lexists(entry / "payload"):
            shutil.rmtree(entry, ignore_errors=True)
        raise

def share_trash_entries(root):
    trash = checked_trash(root)
    if not trash.exists():
        return []
    records = []
    for entry in trash.iterdir():
        try:
            if entry.is_symlink() or getattr(entry, "is_junction", lambda: False)():
                continue
            if not os.path.lexists(entry / "payload"):
                continue
            data = json.loads((entry / "restore.json").read_text(encoding="utf-8"))
            relative = data["original"]
            parts = relative.split("/")
            if not all(valid_name(part) for part in parts) or parts[0].casefold() == NETWORK_TRASH.casefold():
                continue
            records.append((data["deleted"], relative, entry.name))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return sorted(records, reverse=True)

def restore_share_item(root, entry_id):
    if len(entry_id) != 32 or any(c not in "0123456789abcdef" for c in entry_id):
        raise ValueError("Invalid recycle entry")
    root = Path(root)
    trash = checked_trash(root)
    entry = trash / entry_id
    if entry.is_symlink() or getattr(entry, "is_junction", lambda: False)():
        raise ValueError("Invalid recycle entry")
    data = json.loads((entry / "restore.json").read_text(encoding="utf-8"))
    parts = data["original"].split("/")
    if not all(valid_name(part) for part in parts) or parts[0].casefold() == NETWORK_TRASH.casefold():
        raise ValueError("Invalid original path")
    dest = root.joinpath(*parts)
    # Restore only into an existing, ordinary directory, never through a link.
    parent = root
    for part in parts[:-1]:
        parent = parent / part
        if parent.is_symlink() or getattr(parent, "is_junction", lambda: False)():
            raise ValueError("Original parent contains a link or junction")
    if not dest.parent.is_dir():
        raise FileNotFoundError(f"Original parent no longer exists: {dest.parent}")
    if os.path.lexists(dest):
        raise FileExistsError(f"Restore destination already exists: {dest}")
    os.rename(entry / "payload", dest)
    (entry / "restore.json").unlink()
    entry.rmdir()

def recycle(paths):
    errors = []
    for path in paths:
        try:
            root = network_root(path)
            if root is not None:
                share_recycle(path, root)
                continue
            result = QFile.moveToTrash(path)
            ok = result[0] if isinstance(result, tuple) else result
            if not ok:
                errors.append(f"Could not recycle {path}")
        except Exception as exc:
            errors.append(f"{path}: {exc}")
    return errors

def make_zip(paths, dest):
    with zipfile.ZipFile(dest, "x", zipfile.ZIP_DEFLATED, allowZip64=True) as z:
        for source in paths:
            if os.path.islink(source) or getattr(os.path, 'isjunction', lambda _: False)(source):
                raise ValueError(f"Symbolic link not supported: {source}")
            root = Path(source)
            if root.is_dir():
                z.write(root, root.name)
                for base, dirs, files in os.walk(root, followlinks=False):
                    dirs[:] = [d for d in dirs if not Path(base, d).is_symlink()
                               and not getattr(Path(base, d), "is_junction", lambda: False)()]
                    for name in dirs + files:
                        p = Path(base, name)
                        if not p.is_symlink():
                            z.write(p, p.relative_to(root.parent))
            else:
                z.write(root, root.name)
    return []

def zip_members(z):
    """Validate all entries and classify paths before creating anything."""
    members = []
    seen = {}
    for m in z.infolist():
        name = m.orig_filename.replace("\\", "/")
        is_dir = name.endswith("/")
        parts = tuple(name.rstrip("/").split("/"))
        mode = (m.external_attr >> 16) & 0o170000
        if (not all(valid_name(part) and not getattr(os.path, "isreserved", lambda n: False)(part)
                    for part in parts) or mode not in (0, 0o040000, 0o100000)):
            raise ValueError(f"Unsafe ZIP member: {m.orig_filename}")
        key = tuple(normalized(part) for part in parts)
        if key in seen:
            raise ValueError(f"Duplicate ZIP member: {m.filename}")
        seen[key] = is_dir
        members.append((m, parts, is_dir))
    for _, parts, _ in members:
        key = tuple(normalized(part) for part in parts)
        if any(prefix in seen and not seen[prefix]
               for prefix in (key[:i] for i in range(1, len(key)))):
            raise ValueError(f"ZIP file/directory conflict: {'/'.join(parts)}")
    return members

def zip_root(members):
    """A single real or implicit top-level directory, not a lone file."""
    if not members:
        return None
    candidate = members[0][1][0]
    for _, parts, is_dir in members:
        if normalized(parts[0]) != normalized(candidate):
            return None
        if len(parts) == 1 and not is_dir:
            return None
    return candidate

class ZipPasswordRequired(Exception):
    pass

def open_zip_member(z, member, password):
    encrypted = bool(member.flag_bits & 1)
    if encrypted and not password:
        raise ZipPasswordRequired()
    try:
        return z.open(member, pwd=password if encrypted else None)
    except RuntimeError as exc:
        if encrypted and "password" in str(exc).lower():
            raise ZipPasswordRequired() from None
        raise

def extract_members(z, members, target, strip_root=False, password=None):
    target = Path(target)
    if os.path.lexists(target):
        raise FileExistsError(f"Destination already exists: {target}")
    # Stage on the same volume; failed extraction never publishes a partial folder.
    with tempfile.TemporaryDirectory(prefix=".triextract-", dir=target.parent) as tmp:
        staged = Path(tmp, "content")
        staged.mkdir()
        for m, parts, is_dir in members:
            if strip_root:
                parts = parts[1:]
            if is_dir and m.flag_bits & 1:
                with open_zip_member(z, m, password) as src:
                    while src.read(1024 * 1024):
                        pass
            if not parts:
                continue
            dest = staged.joinpath(*parts)
            if is_dir:
                dest.mkdir(parents=True, exist_ok=True)
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                with open_zip_member(z, m, password) as src, dest.open("xb") as dst:
                    shutil.copyfileobj(src, dst, 1024 * 1024)
        if os.path.lexists(target):
            raise FileExistsError(f"Destination already exists: {target}")
        # On Windows rename fails if the destination exists, including during a race.
        staged.rename(target)

def unzip(path, target):
    """Explicit destination (retained for compatibility)."""
    with zipfile.ZipFile(path) as z:
        extract_members(z, zip_members(z), target)
    return []

def smart_unzip(path, destination, password=None, *, report_encrypted=False):
    """Extract atomically, with support for ZipCrypto and WinZip AES."""
    with zipfile.ZipFile(path) as probe:
        aes = any(m.compress_type == 99 for m in probe.infolist())
    zip_class = zipfile.ZipFile
    if aes:
        try:
            from pyzipper import AESZipFile
        except ImportError:
            raise RuntimeError("AES-encrypted ZIP requires pyzipper; install requirements.txt") from None
        zip_class = AESZipFile
    with zip_class(path) as z:
        members = zip_members(z)
        root = zip_root(members)
        name = root if root is not None else Path(path).stem
        if not valid_name(name) or getattr(os.path, "isreserved", lambda n: False)(name):
            raise ValueError(f"Invalid extraction folder: {name}")
        target = Path(destination, name)
        encrypted = any(m.flag_bits & 1 for m, _, _ in members)
        extract_members(z, members, target, strip_root=root is not None, password=password)
    return (target, encrypted) if report_encrypted else target

def archive_signature(path):
    stat = os.stat(path)
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns

@dataclass(frozen=True)
class ExtractionResult:
    password_required: bool = False
    encrypted: bool = False
    error: str | None = None

def extract_one(path, destination, remove_source=False, password=None):
    try:
        if not path.lower().endswith(".zip"):
            raise ValueError("Drag extraction currently supports ZIP archives only")
        original = archive_signature(path) if remove_source else None
        _, encrypted = smart_unzip(path, destination, password, report_encrypted=True)
    except ZipPasswordRequired:
        return ExtractionResult(password_required=True)
    except Exception as exc:
        return ExtractionResult(error=f"{Path(path).name}: {exc}")
    if remove_source:
        try:
            if archive_signature(path) != original:
                raise RuntimeError("Source changed during extraction")
            failures = recycle([path])
            if failures:
                raise RuntimeError("; ".join(failures))
        except Exception as exc:
            return ExtractionResult(encrypted=encrypted,
                error=f"{Path(path).name}: extracted successfully, but the source was not recycled ({exc})")
    return ExtractionResult(encrypted=encrypted)

def extract_archives(paths, destination, remove_source=False):
    errors = []
    for path in paths:
        result = extract_one(path, destination, remove_source)
        if result.password_required:
            errors.append(f"{Path(path).name}: password required")
        elif result.error:
            errors.append(result.error)
    return errors


STYLE = """
QWidget { font-family: 'Segoe UI'; font-size: 10pt; }
QMainWindow, QWidget#shell { background: #f3f5f8; color: #202a39; }
QFrame#pane { background: #ffffff; border: 1px solid #dbe1e9; border-radius: 7px; }
QLabel#heading { font-size: 11pt; font-weight: 600; color: #23354b; }
QLabel#muted { color: #657388; font-size: 9pt; }
QToolButton { background: transparent; border: 1px solid transparent; border-radius: 5px; padding: 5px 9px; }
QToolButton:hover { background: #e6edf7; border-color: #d4e0ee; }
QToolButton:pressed, QToolButton:checked { background: #d5e5fa; }
QToolButton:disabled { color: #a0aab8; }
QLineEdit { background: white; border: 1px solid #cdd6e1; border-radius: 5px; padding: 6px 9px; selection-background-color: #3478c9; }
QLineEdit:focus { border-color: #3478c9; }
QTreeView, QTreeWidget { border: none; background: #ffffff; alternate-background-color: #f7f9fc; outline: 0; selection-background-color: #dceaff; selection-color: #152d50; }
QTreeView::item, QTreeWidget::item { min-height: 27px; padding: 2px; }
QTreeView::item:hover, QTreeWidget::item:hover { background: #edf3fb; }
QTreeView::item:selected, QTreeWidget::item:selected { background: #dceaff; color: #152d50; }
QHeaderView::section { background: #f4f6f9; color: #526174; border: none; border-bottom: 1px solid #dce2eb; padding: 6px; font-size: 9pt; }
QSplitter::handle { background: transparent; }
QSplitter::handle:hover { background: #b9cde7; }
QMenu { background: white; border: 1px solid #d5dce7; padding: 4px; }
QMenu::item { padding: 6px 24px 6px 10px; }
QMenu::item:selected { background: #dceaff; color: #152d50; }
QStatusBar { color: #536174; background: #f3f5f8; }
"""

def svg_icon(body):
    pixel = QPixmap(48, 48)
    pixel.fill(Qt.transparent)
    renderer = QSvgRenderer(('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">' + body + '</svg>').encode())
    painter = QPainter(pixel)
    renderer.render(painter)
    painter.end()
    return QIcon(pixel)

class Explorer(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP)
        self.resize(1450, 820)
        self.setMinimumSize(850, 480)
        self.settings = QSettings("KHzz", APP)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(4)
        self.workers = set()
        self.zip_password = None
        self.extract_queue = deque()
        self.extract_running = False
        self.extract_errors = []
        self.duplicate_job = None
        self.duplicate_progress = None
        self.duplicate_scan_key = None
        self.duplicate_scan_errors = []
        self.duplicate_cleanup = False
        self.archive_match_job = None
        self.archive_match_progress = ""
        self.archive_match_errors = []
        self.archive_pairs = []
        self.archive_match_key = None
        self.size_job = None
        self.size_db = folder_sizes.cache_path()
        self.size_attempted = set()
        self.generation = 0
        self.current = None
        self.history = []
        self.history_index = -1
        self.cut_paths = []
        self.active_view = None
        self.rename_editor = None
        self.rename_busy = False
        self.rename_queue = deque()
        self.rename_view = None
        self.rename_path = None
        self.preview_open = False
        self.preview_job = None
        self.preview_inflight = False
        self.preview_pending = False
        self.preview_key = None
        self.preview_width = self.settings.value("preview_width", 340, type=int)
        self.preview_timer = QTimer(self, singleShot=True, interval=120)
        self.preview_timer.timeout.connect(self.load_preview)
        self.show_hidden = self.settings.value("hidden", False, type=bool)
        self.pins = self.settings.value("pins", [], type=list)
        self.hidden_default_pins = self.settings.value("hidden_default_pins", [], type=list)
        self.tree_epoch = 0
        sp = QStyle.StandardPixmap
        self.icons = {k: self.style().standardIcon(v) for k, v in {
            "folder": sp.SP_DirIcon, "file": sp.SP_FileIcon, "archive": sp.SP_DriveFDIcon,
            "pc": sp.SP_ComputerIcon, "drive": sp.SP_DriveHDIcon,
            "back": sp.SP_ArrowBack, "forward": sp.SP_ArrowForward,
            "up": sp.SP_ArrowUp, "refresh": sp.SP_BrowserReload}.items()}
        self.icons.update({
            "folder": svg_icon('<path d="M2 6V4a1 1 0 0 1 1-1h6l2 3h10a1 1 0 0 1 1 1v13H2z" fill="#e9b656" stroke="#b98531" stroke-linejoin="round"/><path d="M2 8h20v12H2z" fill="#f4ce7d" stroke="#b98531" stroke-linejoin="round"/>'),
            "file": svg_icon('<path d="M5 2h9l5 5v15H5z" fill="#f3f6fa" stroke="#8a9db4" stroke-width="1.4" stroke-linejoin="round"/><path d="M14 2v5h5M8 12h8M8 15h8M8 18h5" fill="none" stroke="#8a9db4" stroke-linecap="round"/>'),
            "archive": svg_icon('<rect x="3" y="2" width="18" height="20" rx="2" fill="#e3eaf9" stroke="#728dbd" stroke-width="1.3"/><path d="M12 3v12" stroke="#526d9e" stroke-width="2" stroke-dasharray="2 1"/><rect x="9.5" y="14" width="5" height="5" rx="1" fill="#fff" stroke="#526d9e"/>')
        })
        self.model = Files(self.icons, self)
        self.watcher = QFileSystemWatcher(self)
        self.refresh_timer = QTimer(self, singleShot=True, interval=250)
        self.refresh_timer.timeout.connect(self.refresh_from_watcher)
        self.watcher.directoryChanged.connect(lambda _: self.refresh_timer.start())
        self.build()
        geometry = self.settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)
        state = self.settings.value("splitter", b"")
        if state:
            self.splitter.restoreState(state)
        side_state = self.settings.value("side_splitter")
        if side_state:
            self.side_splitter.restoreState(side_state)
        self.rebuild_tree()
        if self.settings.value("preview_open", False, type=bool):
            self.preview_button.setChecked(True)
        initial = self.settings.value("location", PC, type=str)
        self.navigate(initial if initial and os.path.isdir(initial) else PC)

    def submit(self, fn, done, progress=None):
        job = Worker(fn if progress is None else lambda: fn(job.signals.progress.emit))
        if progress is not None:
            job.signals.progress.connect(progress, Qt.QueuedConnection)
        self.workers.add(job)
        def finished(result, error):
            self.workers.discard(job)
            done(result, error)
        job.signals.finished.connect(finished, Qt.QueuedConnection)
        self.pool.start(job)

    def tool(self, text, callback, icon=None, tip=None):
        b = QToolButton()
        b.setText(text)
        if icon:
            b.setIcon(self.icons[icon])
            b.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        b.setToolTip(tip or text)
        b.clicked.connect(callback)
        return b

    def heading(self, text, note=None):
        box = QWidget()
        layout = QHBoxLayout(box)
        layout.setContentsMargins(10, 7, 10, 4)
        label = QLabel(text, objectName="heading")
        layout.addWidget(label)
        layout.addStretch()
        if note is not None:
            layout.addWidget(note)
        return box

    def panel(self):
        frame = QFrame(objectName="pane")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(3)
        return frame, layout

    def build(self):
        shell = QWidget(objectName="shell")
        self.setCentralWidget(shell)
        root = QVBoxLayout(shell)
        root.setContentsMargins(10, 8, 10, 4)
        root.setSpacing(8)
        nav = QHBoxLayout()
        self.back_button = self.tool("Back", self.back, "back", "Alt+Left")
        self.forward_button = self.tool("Forward", self.forward, "forward", "Alt+Right")
        self.up_button = self.tool("Up", self.up, "up", "Alt+Up")
        self.home_button = self.tool("Home", self.go_home, "pc", "This PC (Alt+Home)")
        for b in (self.back_button, self.forward_button, self.up_button, self.home_button,
                  self.tool("", self.refresh, "refresh", "Refresh (F5)")):
            nav.addWidget(b)
        self.address = QLineEdit()
        self.address.setPlaceholderText("Enter a folder path")
        self.address.returnPressed.connect(lambda: self.navigate(self.address.text()))
        nav.addWidget(self.address, 1)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter this folder...")
        self.search.setClearButtonEnabled(True)
        self.search.setMaximumWidth(235)
        self.search.textChanged.connect(self.filter_changed)
        nav.addWidget(self.search)
        root.addLayout(nav)
        actions = QHBoxLayout()
        for text, fn in (("New folder", self.new_folder), ("Copy", self.copy),
                         ("Cut", self.cut), ("Paste", self.paste), ("Rename", self.rename),
                         ("Recycle", self.delete), ("Restore", self.restore_recycled), ("Create ZIP", self.create_zip),
                         ("Pin folder", self.pin)):
            actions.addWidget(self.tool(text, fn))
        actions.addStretch()
        hidden = self.tool("Hidden items", self.toggle_hidden)
        hidden.setCheckable(True)
        hidden.setChecked(self.show_hidden)
        actions.addWidget(hidden)
        self.preview_button = QToolButton()
        self.preview_button.setText("Image preview")
        self.preview_button.setCheckable(True)
        self.preview_button.setToolTip("Show or hide image preview (Alt+P)")
        actions.addWidget(self.preview_button)
        root.addLayout(actions)
        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(7)
        root.addWidget(self.splitter, 1)
        side, side_layout = self.panel()
        side.setMinimumWidth(180)
        self.quick = QTreeWidget()
        self.quick.setHeaderHidden(True)
        self.quick.setRootIsDecorated(False)
        self.quick.setIndentation(0)
        self.quick.setContextMenuPolicy(Qt.CustomContextMenu)
        self.quick.customContextMenuRequested.connect(self.quick_menu)
        self.quick.itemClicked.connect(lambda item, _: self.navigate(item.data(0, PATH_ROLE)))
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.tree_menu)
        self.tree.setIndentation(17)
        self.tree.itemExpanded.connect(self.load_tree)
        self.tree.itemClicked.connect(lambda item, _: self.navigate(item.data(0, PATH_ROLE))
                                      if item.data(0, PATH_ROLE) is not None else None)
        self.side_splitter = QSplitter(Qt.Vertical)
        self.side_splitter.setChildrenCollapsible(False)
        self.side_splitter.setHandleWidth(7)
        quick_panel = QWidget()
        quick_layout = QVBoxLayout(quick_panel)
        quick_layout.setContentsMargins(0, 0, 0, 0)
        quick_layout.setSpacing(2)
        quick_layout.addWidget(self.heading("Quick access"))
        quick_layout.addWidget(self.quick, 1)
        self.quick.setMinimumHeight(42)
        tree_panel = QWidget()
        tree_layout = QVBoxLayout(tree_panel)
        tree_layout.setContentsMargins(0, 0, 0, 0)
        tree_layout.setSpacing(2)
        tree_layout.addWidget(self.heading("Folders"))
        tree_layout.addWidget(self.tree, 1)
        self.tree.setMinimumHeight(70)
        self.side_splitter.addWidget(quick_panel)
        self.side_splitter.addWidget(tree_panel)
        self.side_splitter.setSizes([220, 500])
        self.side_splitter.setStretchFactor(0, 0)
        self.side_splitter.setStretchFactor(1, 1)
        side_layout.addWidget(self.side_splitter, 1)
        self.splitter.addWidget(side)
        self.views = []
        self.proxies = []
        self.counts = []
        self.mode_selectors = []
        for i, default_mode in enumerate(("default", "archives")):
            pane, layout = self.panel()
            count = QLabel("0", objectName="muted")
            mode = self.settings.value(f"pane/{i}/mode", default_mode, type=str)
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
            proxy = PaneFilter(mode, self)
            proxy.hidden = self.show_hidden
            proxy.setSourceModel(self.model)
            view.setModel(proxy)
            column = self.settings.value(f"pane/{i}/sort_column", 0, type=int)
            descending = self.settings.value(f"pane/{i}/descending", False, type=bool)
            view.sortByColumn(column if 0 <= column < 4 else 0,
                              Qt.DescendingOrder if descending else Qt.AscendingOrder)
            header = view.header()
            header.setSectionsMovable(True)
            header.setStretchLastSection(False)
            header.setSectionResizeMode(0, QHeaderView.Stretch)
            header.resizeSection(1, 82)
            header.resizeSection(2, 78)
            header.resizeSection(3, 145)
            view.doubleClicked.connect(lambda i, v=view: self.double_click_entry(v, i))
            view.clicked.connect(lambda _, v=view: self.set_active(v))
            view.selectionModel().selectionChanged.connect(lambda *_args, v=view: self.selection_changed(v))
            view.customContextMenuRequested.connect(lambda pos, v=view: self.context(v, pos))
            view.dropped.connect(lambda paths, target, move: self.do_transfer(paths, target or self.current, move))
            view.archive_dropped.connect(lambda paths, target: self.do_extract(paths, target or self.current, remove_source=True))
            view.setToolTip("Drag ZIP files to the opposite pane to extract and recycle the ZIP. Drop on a folder to extract inside it.")
            layout.addWidget(view, 1)
            self.splitter.addWidget(pane)
            self.views.append(view)
            self.proxies.append(proxy)
            self.counts.append(count)
            self.mode_selectors.append(selector)
            selector.currentIndexChanged.connect(lambda _, idx=i: self.change_pane_mode(idx))
        self.active_view = self.views[0]
        self.preview_panel, preview_layout = self.panel()
        self.preview_panel.setMinimumWidth(0)
        close_preview = self.tool("Hide", lambda: self.preview_button.setChecked(False))
        preview_layout.addWidget(self.heading("Image preview", close_preview))
        self.preview_label = PreviewLabel()
        preview_layout.addWidget(self.preview_label, 1)
        self.preview_details = QLabel("", objectName="muted")
        self.preview_details.setWordWrap(True)
        self.preview_details.setContentsMargins(9, 4, 9, 8)
        preview_layout.addWidget(self.preview_details)
        self.splitter.addWidget(self.preview_panel)
        self.splitter.setStretchFactor(3, 0)
        self.preview_panel.hide()
        self.preview_animation = QPropertyAnimation(self.preview_panel, b"maximumWidth", self)
        self.preview_animation.setDuration(150)
        self.preview_animation.setEasingCurve(QEasingCurve.OutCubic)
        self.preview_animation.finished.connect(self.preview_animation_finished)
        self.preview_button.toggled.connect(self.set_preview_open)
        self.splitter.setSizes([245, 680, 510, 0])
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 3)
        self.splitter.setStretchFactor(2, 2)
        self.build_quick()
        for key, fn in (("Alt+Left", self.back), ("Alt+Right", self.forward), ("Alt+Up", self.up),
                        ("F5", self.refresh), ("Alt+P", lambda: self.preview_button.toggle()), ("Alt+Home", self.go_home), ("Ctrl+L", self.focus_address),
                        ("Ctrl+F", self.search.setFocus), ("Ctrl+Shift+N", self.new_folder),
                        ("F2", self.rename), ("Delete", self.delete), ("Ctrl+C", self.copy),
                        ("Ctrl+X", self.cut), ("Ctrl+V", self.paste), ("Return", self.open_selected),
                        ("Backspace", self.up), ("Ctrl+A", self.select_all)):
            action = QAction(self)
            action.setShortcut(QKeySequence(key))
            if key in ("Return", "Backspace", "Ctrl+A", "Ctrl+C", "Ctrl+X", "Ctrl+V", "Delete", "F2"):
                action.setShortcutContext(Qt.WidgetWithChildrenShortcut)
                for view in self.views:
                    view.addAction(action)
            else:
                self.addAction(action)
            action.triggered.connect(fn)

    def editing(self):
        return isinstance(QApplication.focusWidget(), QLineEdit)

    def focus_address(self):
        self.address.setFocus()
        self.address.selectAll()

    def change_pane_mode(self, i):
        if self.rename_editor is not None:
            self.end_rename()
        mode = self.mode_selectors[i].currentData()
        proxy = self.proxies[i]
        proxy.set_mode(mode)
        if mode in ("alphabetical", "archive_matches"):
            self.views[i].sortByColumn(0, Qt.AscendingOrder)
        self.update_counts()
        self.settings.setValue(f"pane/{i}/mode", mode)
        if mode == "archive_matches":
            self.scan_archive_matches()
        elif not any(p.mode == "archive_matches" for p in self.proxies):
            self.cancel_archive_matches()
        if mode == "duplicates":
            self.scan_duplicate_view()
        elif not any(p.mode == "duplicates" for p in self.proxies):
            self.cancel_duplicate_view()
            if self.size_job is None:
                self.refresh_folder_sizes()

    def cancel_duplicate_view(self):
        self.duplicate_scan_key = None
        if self.duplicate_job is not None:
            self.duplicate_job.set()
            self.duplicate_job = None

    def show_duplicates(self, view):
        i = self.views.index(view)
        selector = self.mode_selectors[i]
        if selector.currentData() == "duplicates":
            self.scan_duplicate_view()
        else:
            selector.setCurrentIndex(selector.findData("duplicates"))

    def scan_duplicate_view(self, force=True):
        scan_key = (normalized(self.current), self.show_hidden, tuple(sorted(
            (normalized(e.path), e.size if e.size is not None else -1,
             e.modified if e.modified is not None else -1, e.hidden)
            for e in self.model.entries if not e.folder)))
        if not force and self.duplicate_scan_key == scan_key:
            return
        self.cancel_duplicate_view()
        self.duplicate_scan_key = scan_key
        for proxy in self.proxies:
            proxy.duplicate_paths = set()
            if proxy.mode == "duplicates":
                proxy.invalidate()
        self.update_counts()
        if not self.current or self.duplicate_cleanup or not any(p.mode == "duplicates" for p in self.proxies):
            return
        path = self.current
        self.cancel_size_scan()
        stop = Event()
        self.duplicate_job = stop
        self.duplicate_progress = "Finding candidates..."
        self.duplicate_scan_errors = []
        self.update_counts()
        self.statusBar().showMessage("Checking matching file sizes and SHA-256...")
        def done(result, error):
            if stop.is_set() or self.duplicate_job is not stop:
                return
            self.duplicate_job = None
            self.duplicate_progress = None
            self.duplicate_scan_errors = [error] if error else result.errors
            if error:
                self.update_counts()
                self.statusBar().showMessage("Duplicate scan failed: " + error)
                self.refresh_folder_sizes()
                return
            paths = {normalized(item.path) for keep, copies in result.groups for item in [keep, *copies]}
            for proxy in self.proxies:
                proxy.duplicate_paths = paths
                if proxy.mode == "duplicates":
                    proxy.invalidate()
            self.update_counts()
            self.statusBar().showMessage(f"{len(result.groups)} duplicate groups; {len(paths)} matching files"
                                         + (f"; {len(result.errors)} skipped/errors" if result.errors else ""))
            self.refresh_folder_sizes()
        def progress(checked, total, new_groups):
            if self.duplicate_job is stop and not stop.is_set():
                if new_groups:
                    paths = {normalized(item.path) for keep, copies in new_groups for item in [keep, *copies]}
                    for proxy in self.proxies:
                        proxy.duplicate_paths.update(paths)
                        if proxy.mode == "duplicates":
                            proxy.invalidate()
                self.duplicate_progress = f"SHA: {checked:,}/{total:,}"
                self.update_counts()
        hidden = self.show_hidden
        self.submit(lambda emit: duplicates.find_duplicates(path, show_hidden=hidden,
                    cancelled=stop.is_set, progress=lambda n, total: emit(n, total, None),
                    matches=lambda groups, n, total: emit(n, total, groups),
                    cache_db=self.size_db), done, progress)

    def cancel_archive_matches(self):
        self.archive_match_key = None
        if self.archive_match_job is not None:
            self.archive_match_job.set()
            self.archive_match_job = None

    def show_archive_matches(self, view, with_password=False):
        selector = self.mode_selectors[self.views.index(view)]
        if with_password:
            password, ok = QInputDialog.getText(self, "Verify ZIP contents", "ZIP password:", QLineEdit.Password)
            if not ok:
                return
            selector.blockSignals(True)
            selector.setCurrentIndex(selector.findData("archive_matches"))
            selector.blockSignals(False)
            self.proxies[self.views.index(view)].set_mode("archive_matches")
            view.sortByColumn(0, Qt.AscendingOrder)
            self.settings.setValue(f"pane/{self.views.index(view)}/mode", "archive_matches")
            self.scan_archive_matches(password_override=password.encode("utf-8"))
        elif selector.currentData() == "archive_matches":
            self.scan_archive_matches()
        else:
            selector.setCurrentIndex(selector.findData("archive_matches"))

    def scan_archive_matches(self, force=True, password_override=None):
        if self.duplicate_cleanup or not self.current or not any(p.mode == "archive_matches" for p in self.proxies):
            return
        scan_key = (normalized(self.current), self.show_hidden, tuple(sorted(
            (normalized(e.path), e.size if e.size is not None else -1,
             e.modified if e.modified is not None else -1)
            for e in self.model.entries)))
        if not force and self.archive_match_key == scan_key:
            return
        self.cancel_archive_matches()
        self.cancel_size_scan()
        self.archive_match_key = scan_key
        self.model.archive_notes = {}
        self.archive_pairs = []
        for proxy in self.proxies:
            proxy.archive_match_paths.clear()
            if proxy.mode == "archive_matches":
                proxy.invalidate()
        entries = [e for e in self.model.entries if self.show_hidden or not e.hidden]
        folders = [e.path for e in entries if e.folder]
        archives = [(e.path,e.size) for e in entries if not e.folder and e.name.lower().endswith(".zip") and e.size is not None]
        sizes = dict(self.model.folder_sizes)
        password = self.zip_password if password_override is None else password_override
        stop = Event()
        self.archive_match_job = stop
        self.archive_match_progress = "Checking sizes..."
        self.archive_match_errors = []
        self.update_counts()
        def add(match):
            for item, other in ((match.archive, match.folder),(match.folder, match.archive)):
                key = normalized(item)
                note = f"{Path(other).name}: {match.status}"
                self.model.archive_notes[key] = (self.model.archive_notes.get(key, "") + "\n" + note).strip()
                for proxy in self.proxies:
                    proxy.archive_match_paths.add(key)
                    if proxy.mode == "archive_matches":
                        proxy.invalidate()
        def progress(n,total,match):
            if self.archive_match_job is not stop or stop.is_set():
                return
            if match:
                self.archive_pairs.append(match)
                add(match)
            self.archive_match_progress = f"ZIP: {n}/{total}"
            self.update_counts()
        def done(result,error):
            if self.archive_match_job is not stop or stop.is_set():
                return
            self.archive_match_job = None
            self.archive_match_errors = [error] if error else result[1]
            if not error:
                matches = result[0]
                self.archive_pairs = matches
                if password is not None and any(m.verified and m.encrypted for m in matches):
                    self.zip_password = password
                for path, size in result[2].items():
                    self.model.set_folder_size(path, size)
                self.archive_match_progress = f"{sum(m.verified for m in matches)} verified / {len(matches)} pairs"
            else:
                self.archive_match_progress = "Scan failed"
            self.update_counts()
            self.refresh_folder_sizes()
        folder_rows = [(e.path, e.modified) for e in entries if e.folder]
        def run(emit):
            matches, errors = archive_matches.find_matches(folders, archives, sizes, self.size_db,
                zip_members, password, stop.is_set, lambda n,total,m: emit(n,total,m))
            try:
                updated = folder_sizes.lookup(self.size_db, folder_rows)
            except (OSError, sqlite3.Error):
                updated = {}
            return matches, errors, updated
        self.submit(run, done, progress)

    def cleanup_archive_pairs(self, preferred=None):
        if self.duplicate_cleanup or not self.current:
            return
        if self.archive_match_job is not None:
            self.statusBar().showMessage("Wait for the ZIP / folder scan to finish before cleanup.")
            return
        pairs = list(self.archive_pairs)
        if preferred:
            pairs = [m for m in pairs if normalized(preferred) in (normalized(m.archive), normalized(m.folder))]
        if not pairs:
            self.statusBar().showMessage("No ZIP / folder pairs to clean up.")
            return
        grouped = {}
        for match in pairs:
            grouped.setdefault(normalized(match.archive), []).append(match)
        queue = deque(grouped.values())
        self.cancel_duplicate_view()
        self.cancel_archive_matches()
        self.cancel_size_scan()
        self.duplicate_cleanup = True
        removed = 0
        issues = []
        total = len(queue)
        def finish():
            self.duplicate_cleanup = False
            self.refresh()
            self.refresh_tree_branch()
            message = f"Recycled {removed} matching ZIP(s); extracted folders kept."
            self.statusBar().showMessage(message)
            if issues:
                self.warn(message + "\n\n" + "\n".join(issues))
        def next_group():
            if not queue:
                finish()
                return
            group = queue.popleft()
            verify(group, self.zip_password)
        def verify(group, password):
            name = Path(group[0].archive).name
            self.statusBar().showMessage(f"Verifying {total-len(queue)}/{total}: {name}")
            def run():
                pending = None
                errors = []
                for match in group:
                    try:
                        before = duplicates.record(match.archive).signature
                        checked = archive_matches.compare(match.archive, match.folder, zip_members, password)
                        if checked and checked.verified:
                            if duplicates.record(match.archive).signature != before:
                                raise ValueError("ZIP changed after verification")
                            failures = recycle([match.archive])
                            return (not failures, checked.encrypted, None, failures)
                        if checked and checked.password_required:
                            pending = checked
                        elif checked:
                            errors.append(name + ": " + checked.status)
                    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile, NotImplementedError) as exc:
                        errors.append(name + ": " + str(exc))
                return (False, False, pending, errors)
            def done(result, error):
                nonlocal removed
                if error:
                    issues.append(name + ": " + error)
                else:
                    success, encrypted, pending, errors = result
                    if pending is not None:
                        replacement = self.ask_zip_password(pending.archive, password is not None)
                        if replacement is not None:
                            verify(group, replacement)
                            return
                        issues.append(name + ": skipped; password required")
                    elif success:
                        removed += 1
                        if encrypted:
                            self.zip_password = password
                    else:
                        issues.extend(errors or [name + ": no longer an exact content match; retained"])
                next_group()
            self.submit(run, done)
        next_group()

    def cleanup_duplicates(self, recursive=False, preferred=None):
        if not self.current or self.duplicate_cleanup:
            return
        if recursive and QMessageBox.question(self, APP,
                f"Recycle exact duplicates in this folder AND every subfolder?\n{self.current}\n"
                "One file per SHA-256 group will remain. Continue?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        path = self.current
        show_hidden = self.show_hidden
        self.cancel_duplicate_view()
        self.cancel_size_scan()
        self.duplicate_cleanup = True
        self.statusBar().showMessage("Checking sizes and SHA-256; recycling exact duplicates...")
        def run():
            result = duplicates.find_duplicates(path, recursive, preferred, show_hidden)
            return duplicates.remove_duplicates(result, recycle)
        def done(result, error):
            self.duplicate_cleanup = False
            if error:
                self.warn(error)
            elif result[1]:
                self.warn(f"Recycled {result[0]} duplicate(s). Skipped/errors:\n" + "\n".join(result[1]))
            self.refresh()
            self.refresh_tree_branch()
            if not error:
                self.statusBar().showMessage(f"Recycled {result[0]} duplicate(s)")
        self.submit(run, done)

    def build_quick(self):
        self.quick.clear()
        defaults = [("This PC", PC)]
        for name, location in (("Home", QStandardPaths.HomeLocation),
                               ("Desktop", QStandardPaths.DesktopLocation),
                               ("Documents", QStandardPaths.DocumentsLocation),
                               ("Downloads", QStandardPaths.DownloadLocation),
                               ("Pictures", QStandardPaths.PicturesLocation)):
            path = QStandardPaths.writableLocation(location)
            if path:
                defaults.append((name, path))
        self.default_pins = {normalized(path): name for name, path in defaults}
        hidden = set(self.hidden_default_pins)
        locations = [(name, path) for name, path in defaults if normalized(path) not in hidden]
        seen = {normalized(path) for _, path in locations}
        for path in self.pins:
            key = normalized(path)
            if key not in seen and key not in hidden:
                locations.append((self.default_pins.get(key, os.path.basename(path.rstrip("\\/")) or path), path))
                seen.add(key)
        self.visible_pins = seen
        for name, path in locations:
            item = QTreeWidgetItem([name])
            item.setIcon(0, self.icons["pc" if not path else "folder"])
            item.setData(0, PATH_ROLE, path)
            item.setToolTip(0, path or "This PC")
            self.quick.addTopLevelItem(item)
        if not locations:
            item = QTreeWidgetItem(["Right-click to add folders"])
            item.setFlags(Qt.NoItemFlags)
            self.quick.addTopLevelItem(item)

    def pin_action(self, menu, path):
        if path is None:
            return
        if normalized(path) in self.visible_pins:
            menu.addAction("Unpin from Quick Access", lambda: self.unpin(path))
        else:
            menu.addAction("Pin to Quick Access", lambda: self.add_pin(path))

    def quick_menu(self, pos):
        item = self.quick.itemAt(pos)
        menu = QMenu(self)
        if item and item.data(0, PATH_ROLE) is not None:
            self.pin_action(menu, item.data(0, PATH_ROLE))
            menu.addSeparator()
        if self.current is not None and normalized(self.current) not in self.visible_pins:
            menu.addAction("Pin current folder", self.pin)
        menu.addAction("Add pinned folder...", self.browse_pin)
        if self.hidden_default_pins:
            menu.addAction("Restore default shortcuts", self.restore_default_pins)
        menu.exec(self.quick.viewport().mapToGlobal(pos))

    def tree_menu(self, pos):
        item = self.tree.itemAt(pos)
        if item:
            menu = QMenu(self)
            self.pin_action(menu, item.data(0, PATH_ROLE))
            if not menu.isEmpty():
                menu.exec(self.tree.viewport().mapToGlobal(pos))

    def save_pins(self):
        self.settings.setValue("pins", self.pins)
        self.settings.setValue("hidden_default_pins", self.hidden_default_pins)
        self.settings.sync()
        self.build_quick()

    def add_pin(self, path):
        if path is None:
            return
        path = os.path.abspath(path) if path else PC
        key = normalized(path)
        if key in self.visible_pins:
            return
        self.hidden_default_pins = [p for p in self.hidden_default_pins if p != key]
        if key not in self.default_pins and not any(normalized(p) == key for p in self.pins):
            self.pins.append(path)
        self.save_pins()

    def browse_pin(self):
        path = QFileDialog.getExistingDirectory(self, "Add pinned folder", self.current or "")
        if path:
            self.add_pin(path)

    def unpin(self, path):
        key = normalized(path)
        self.pins = [p for p in self.pins if normalized(p) != key]
        if key in self.default_pins and key not in self.hidden_default_pins:
            self.hidden_default_pins.append(key)
        self.save_pins()

    def restore_default_pins(self):
        self.hidden_default_pins = []
        self.save_pins()

    def pin(self):
        self.add_pin(self.current)

    def rebuild_tree(self):
        self.tree_epoch += 1
        self.tree.clear()
        self.pc_item = QTreeWidgetItem(["This PC"])
        self.pc_item.setData(0, PATH_ROLE, PC)
        self.pc_item.setIcon(0, self.icons["pc"])
        self.pc_item.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
        self.tree.addTopLevelItem(self.pc_item)
        self.pc_item.setExpanded(True)
        self.load_tree(self.pc_item)

    def load_tree(self, item):
        if item.data(0, LOADED_ROLE):
            return
        path = item.data(0, PATH_ROLE)
        if path is None:
            return
        item.setData(0, LOADED_ROLE, "loading")
        epoch = self.tree_epoch
        def done(entries, error):
            if epoch != self.tree_epoch:
                return
            # A removed item can be invalidated while a worker is running.
            try:
                if item.treeWidget() is not self.tree:
                    return
            except RuntimeError:
                return
            item.takeChildren()
            if error:
                item.setData(0, LOADED_ROLE, None)
                item.setToolTip(0, error)
                return
            for e in sorted((e for e in entries if e.folder and (self.show_hidden or not e.hidden)),
                            key=lambda e: e.name.casefold()):
                child = QTreeWidgetItem([e.name])
                child.setData(0, PATH_ROLE, e.path)
                child.setIcon(0, self.icons["drive" if path == PC else "folder"])
                child.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
                child.setToolTip(0, e.path)
                item.addChild(child)
            item.setData(0, LOADED_ROLE, "loaded")
            if not item.childCount():
                item.setChildIndicatorPolicy(QTreeWidgetItem.DontShowIndicator)
            self.sync_tree()
        self.submit(lambda: scan(path), done)

    def sync_tree(self):
        target = normalized(self.current)
        item = self.pc_item
        while item is not None:
            path = normalized(item.data(0, PATH_ROLE))
            if path == target:
                self.tree.setCurrentItem(item)
                self.tree.scrollToItem(item)
                return
            if item.data(0, LOADED_ROLE) != "loaded":
                item.setExpanded(True)
                self.load_tree(item)
                return
            match = None
            for i in range(item.childCount()):
                child = item.child(i)
                p = normalized(child.data(0, PATH_ROLE))
                try:
                    if os.path.commonpath([p, target]) == p:
                        match = child
                        break
                except ValueError:
                    pass
            if match is None:
                return
            item.setExpanded(True)
            item = match

    def navigate(self, path, record=True):
        if self.rename_busy:
            return
        if self.rename_editor is not None:
            self.end_rename(refresh=False)
        if path is None:
            return
        path = os.path.expandvars(os.path.expanduser(path.strip().strip('"')))
        if path.casefold() in ("this pc", "my pc", "computer"):
            path = PC
        if path:
            path = os.path.abspath(path)
        if record and normalized(path) == normalized(self.current) and self.current is not None:
            return
        self.current = path
        if record:
            del self.history[self.history_index + 1:]
            self.history.append(path)
            self.history_index = len(self.history) - 1
        self.address.setText(path or "This PC")
        self.search.clear()
        self.back_button.setEnabled(self.history_index > 0)
        self.forward_button.setEnabled(self.history_index < len(self.history) - 1)
        self.up_button.setEnabled(bool(path))
        self.sync_tree()
        self.load_folder(clear=True)

    def go_home(self):
        self.navigate(PC)

    def back(self):
        if self.rename_busy:
            return
        if self.history_index > 0:
            self.history_index -= 1
            self.navigate(self.history[self.history_index], False)

    def forward(self):
        if self.rename_busy:
            return
        if self.history_index < len(self.history) - 1:
            self.history_index += 1
            self.navigate(self.history[self.history_index], False)

    def up(self):
        if self.editing() and QApplication.focusWidget() is self.search:
            return
        if self.current:
            parent = os.path.dirname(self.current)
            self.navigate(parent if parent != self.current else PC)

    def refresh_tree_branch(self):
        item = self.tree.currentItem()
        if item and normalized(item.data(0, PATH_ROLE)) == normalized(self.current):
            item.setData(0, LOADED_ROLE, None)
            item.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
            if item.isExpanded():
                self.load_tree(item)

    def refresh_from_watcher(self):
        self.load_folder()
        self.refresh_tree_branch()

    def refresh(self):
        self.cancel_duplicate_view()
        self.cancel_archive_matches()
        self.load_folder()

    def load_folder(self, clear=False):
        if self.rename_editor is not None or self.rename_busy:
            return
        path = self.current
        if path is None:
            return
        if clear:
            self.cancel_duplicate_view()
            self.cancel_archive_matches()
            self.model.archive_notes = {}
            self.archive_pairs = []
            self.cancel_size_scan()
            self.size_attempted.clear()
        self.generation += 1
        token = self.generation
        selected = [[e.path for e in self.selected(v)] for v in self.views] if not clear else [[], []]
        if clear:
            self.model.replace([])
            self.update_counts()
        self.statusBar().showMessage("Loading " + (path or "This PC") + "...")
        if self.watcher.directories():
            self.watcher.removePaths(self.watcher.directories())
        def done(result, error):
            if token != self.generation:
                return
            if error:
                self.model.replace([])
                self.update_counts()
                self.statusBar().showMessage("Cannot read folder: " + error)
                return
            entries, sizes = result
            self.model.replace(entries, sizes)
            for view, paths in zip(self.views, selected):
                if paths:
                    wanted = set(paths)
                    proxy = view.model()
                    for row in range(proxy.rowCount()):
                        i = proxy.index(row, 0)
                        if i.data(PATH_ROLE) in wanted:
                            view.selectionModel().select(i, view.selectionModel().SelectionFlag.Select |
                                                         view.selectionModel().SelectionFlag.Rows)
            if path:
                self.watcher.addPath(path)
            self.update_counts()
            self.schedule_preview()
            self.statusBar().showMessage(path or "This PC")
            if any(p.mode == "duplicates" for p in self.proxies):
                self.scan_duplicate_view(force=False)
            if any(p.mode == "archive_matches" for p in self.proxies):
                self.scan_archive_matches(force=False)
            if self.size_job is None:
                self.refresh_folder_sizes()
        def list_with_sizes():
            entries = scan(path)
            try:
                sizes = folder_sizes.lookup(self.size_db, [(e.path, e.modified) for e in entries if e.folder]) if path else {}
            except (OSError, ValueError, sqlite3.Error):
                sizes = {}
            return entries, sizes
        self.submit(list_with_sizes, done)

    def cancel_size_scan(self):
        if self.size_job is not None:
            self.size_job.set()
            self.size_job = None

    def refresh_folder_sizes(self, force=False, only=None):
        self.cancel_size_scan()
        if not self.current or self.duplicate_job is not None or self.duplicate_cleanup or self.archive_match_job is not None:
            return  # Never compete with a duplicate scan or recursively walk This PC.
        paths = []
        for e in self.model.entries:
            if not e.folder or (only and normalized(e.path) != normalized(only)):
                continue
            if e.hidden and not self.show_hidden and not force:
                continue
            k = normalized(e.path)
            cached = self.model.folder_sizes.get(k)
            if force or ((not cached or cached.stale) and k not in self.size_attempted):
                paths.append(e.path)
        if not paths:
            return
        stop = Event()
        self.size_job = stop
        self.statusBar().showMessage("Calculating uncached/stale folder sizes...")
        def progress(path, value, error):
            if self.size_job is not stop or stop.is_set():
                return
            if value is not None:
                self.model.set_folder_size(path, value)
                self.size_attempted.discard(path)
            else:
                self.size_attempted.add(path)
        def done(result, error):
            if self.size_job is not stop or stop.is_set():
                return
            self.size_job = None
            if error:
                self.statusBar().showMessage("Folder size cache: " + error)
            elif result:
                self.statusBar().showMessage(f"Folder sizes: {len(result)} incomplete; right-click to retry")
            else:
                self.statusBar().showMessage("Folder sizes up to date")
        self.submit(lambda emit: folder_sizes.refresh(self.size_db, paths, stop.is_set, emit), done, progress)


    def filter_changed(self, text):
        if self.rename_editor is not None and not self.rename_busy:
            self.end_rename()
        for proxy in self.proxies:
            if hasattr(proxy, "endFilterChange"):
                proxy.beginFilterChange()
                proxy.query = text.casefold()
                proxy.endFilterChange(QSortFilterProxyModel.Direction.Rows)
            else:
                proxy.query = text.casefold()
                proxy.invalidateFilter()
        self.update_counts()

    def toggle_hidden(self, checked):
        self.show_hidden = checked
        self.settings.setValue("hidden", checked)
        for proxy in self.proxies:
            if hasattr(proxy, "endFilterChange"):
                proxy.beginFilterChange()
                proxy.hidden = checked
                proxy.endFilterChange(QSortFilterProxyModel.Direction.Rows)
            else:
                proxy.hidden = checked
                proxy.invalidateFilter()
        self.rebuild_tree()
        self.update_counts()
        if any(p.mode == "duplicates" for p in self.proxies):
            self.scan_duplicate_view()
        if any(p.mode == "archive_matches" for p in self.proxies):
            self.scan_archive_matches()

    def update_counts(self):
        for proxy, label in zip(self.proxies, self.counts):
            if proxy.mode == "archive_matches":
                label.setText(self.archive_match_progress or "ZIP / folder matches")
                label.setToolTip("\n".join(self.archive_match_errors[:30]) or "Hover matched rows for verification details")
            elif proxy.mode == "duplicates" and self.duplicate_job is not None:
                label.setText(f"{proxy.rowCount():,} matches | {self.duplicate_progress or 'Scanning...'}")
                label.setToolTip("Checking exact SHA-256 matches; results appear as groups finish")
            else:
                text = f"{proxy.rowCount():,} items"
                errors = self.duplicate_scan_errors if proxy.mode == "duplicates" else []
                if errors:
                    text += f" ({len(errors)} skipped)"
                label.setText(text)
                label.setToolTip("\n".join(errors[:30]))

    def set_active(self, view):
        self.active_view = view
        self.schedule_preview()

    def selected_view(self):
        focus = QApplication.focusWidget()
        for view in self.views:
            if focus is view or (focus and view.isAncestorOf(focus)):
                self.active_view = view
        return self.active_view

    def selection_changed(self, view):
        if view.hasFocus():
            self.active_view = view
        if view is self.active_view:
            self.schedule_preview()
            entries = self.selected(view)
            if entries:
                self.statusBar().showMessage(f"{len(entries)} selected  |  {readable_size(sum(e.size or 0 for e in entries))}")

    def selected(self, view=None):
        view = view or self.selected_view()
        proxy = view.model()
        return [self.model.entry(proxy.mapToSource(i)) for i in view.selectionModel().selectedRows()]

    def select_all(self):
        if not self.editing():
            self.selected_view().selectAll()

    def open_entry(self, view, index):
        self.set_active(view)
        e = self.model.entry(view.model().mapToSource(index))
        if e:
            if e.folder:
                self.navigate(e.path)
            elif not QDesktopServices.openUrl(QUrl.fromLocalFile(e.path)):
                self.warn("Could not open " + e.path)

    def open_selected(self):
        if not self.editing():
            view = self.selected_view()
            rows = view.selectionModel().selectedRows()
            if rows:
                self.open_entry(view, rows[0])

    def warn(self, text):
        QMessageBox.warning(self, APP, text)

    def context(self, view, pos):
        self.set_active(view)
        index = view.indexAt(pos)
        if index.isValid() and not view.selectionModel().isSelected(index):
            view.clearSelection()
            view.selectionModel().select(index, view.selectionModel().SelectionFlag.Select |
                                         view.selectionModel().SelectionFlag.Rows)
        menu = QMenu(self)
        actions = [("Open", self.open_selected), ("Copy", self.copy), ("Cut", self.cut),
                   ("Paste", self.paste), ("Rename", self.rename), ("Recycle", self.delete),
                   ("Copy path", self.copy_path), ("Create ZIP", self.create_zip),
                   ("Extract ZIP to folder", self.extract_zip), ("New folder", self.new_folder),
                   ("Open in Windows Explorer", self.open_location), ("Refresh", self.refresh)]
        for label, fn in actions:
            a = menu.addAction(label)
            a.triggered.connect(fn)
        entry = self.model.entry(view.model().mapToSource(index)) if index.isValid() else None
        if entry and entry.folder:
            menu.addSeparator()
            self.pin_action(menu, entry.path)
        elif not index.isValid() and self.current is not None:
            menu.addSeparator()
            self.pin_action(menu, self.current)
        if self.current:
            menu.addSeparator()
            menu.addAction("Recalculate folder sizes", lambda: self.refresh_folder_sizes(True))
            if entry and entry.folder:
                menu.addAction("Recalculate this folder size", lambda: self.refresh_folder_sizes(True, entry.path))
            if entry and not entry.folder:
                keep_action = menu.addAction("Keep this file, recycle SHA-256 duplicates",
                                             lambda: self.cleanup_duplicates(preferred=entry.path))
                keep_action.setEnabled(not self.duplicate_cleanup)
            dup = menu.addMenu("Duplicates (SHA-256)")
            dup.setEnabled(not self.duplicate_cleanup)
            dup.addAction("Find duplicates", lambda: self.show_duplicates(view))
            dup.addAction("Find ZIP / folder matches", lambda: self.show_archive_matches(view))
            dup.addAction("Verify ZIP / folder matches with password...", lambda: self.show_archive_matches(view, True))
            if self.archive_match_errors:
                dup.addAction("Show ZIP/folder scan errors", lambda: self.warn("\n".join(self.archive_match_errors[:100])))
            if self.duplicate_scan_errors:
                dup.addAction("Show skipped files / scan errors", lambda: self.warn("\n".join(self.duplicate_scan_errors[:100])))
            if view.model().mode == "archive_matches":
                cleanup = dup.addAction("Recycle matching ZIPs - keep folders", lambda: self.cleanup_archive_pairs())
                cleanup.setEnabled(self.archive_match_job is None and bool(self.archive_pairs))
                if entry:
                    action = menu.addAction("Recycle matching ZIP(s) - keep folder",
                                            lambda: self.cleanup_archive_pairs(entry.path))
                    action.setEnabled(self.archive_match_job is None and not self.duplicate_cleanup)
                exact = dup.addAction("Recycle identical files - keep one", lambda: self.cleanup_duplicates())
            else:
                dup.addAction("Recycle duplicates - keep one", lambda: self.cleanup_duplicates())
            dup.addAction("Recycle duplicates recursively...", lambda: self.cleanup_duplicates(True))
            if entry and not entry.folder:
                dup.addAction("Keep this file, recycle matching copies",
                              lambda: self.cleanup_duplicates(preferred=entry.path))
        menu.exec(view.viewport().mapToGlobal(pos))

    def copy_path(self):
        paths = [e.path for e in self.selected()]
        if paths:
            QApplication.clipboard().setText("\n".join(paths))

    def open_location(self):
        if self.current:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.current))

    def clipboard(self, cut=False):
        if self.editing():
            return
        paths = [e.path for e in self.selected() if e.path != os.path.abspath(os.path.splitdrive(e.path)[0] + os.sep)]
        if not paths:
            return
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(p) for p in paths])
        # Explorer's Preferred DropEffect clipboard format.
        mime.setData('application/x-qt-windows-mime;value="Preferred DropEffect"',
                     bytes([2 if cut else 1, 0, 0, 0]))
        QApplication.clipboard().setMimeData(mime)
        self.cut_paths = paths if cut else []
        self.statusBar().showMessage(f"{len(paths)} items {'cut' if cut else 'copied'}")

    def copy(self):
        self.clipboard()

    def cut(self):
        self.clipboard(True)

    def paste(self):
        if self.editing() or not self.current:
            return
        mime = QApplication.clipboard().mimeData()
        if mime and mime.hasUrls():
            paths = [u.toLocalFile() for u in mime.urls() if u.isLocalFile()]
            effect = bytes(mime.data('application/x-qt-windows-mime;value="Preferred DropEffect"'))
            move = paths == self.cut_paths or (len(effect) >= 4 and int.from_bytes(effect[:4], "little") == 2)
            self.do_transfer(paths, self.current, move)

    def operation(self, label, fn, after=None):
        self.statusBar().showMessage(label + "...")
        def done(result, error):
            if error or result:
                self.warn(error or "\n".join(result))
            elif after:
                after()
            self.refresh()
            self.refresh_tree_branch()
        self.submit(fn, done)

    def do_transfer(self, paths, target, move):
        if not target or not paths:
            return
        if move and not QMessageBox.question(self, APP, f"Move {len(paths)} item(s) to:\n{target}") == QMessageBox.Yes:
            return
        self.operation("Moving" if move else "Copying", lambda: transfer(paths, target, move),
                       lambda: self.cut_paths.clear() if move else None)

    def new_folder(self):
        if not self.current:
            return
        name, ok = QInputDialog.getText(self, "New folder", "Folder name:")
        if ok:
            if not valid_name(name):
                self.warn("Invalid folder name")
                return
            path = os.path.join(self.current, name)
            self.operation("Creating folder", lambda: os.mkdir(path))

    def double_click_entry(self, view, index):
        entry = self.model.entry(view.model().mapToSource(index))
        if entry and entry.folder:
            self.open_entry(view, index)
        elif entry:
            self.begin_rename(view, index)

    def begin_rename(self, view, index):
        if not self.current or self.rename_busy or self.duplicate_cleanup:
            return
        if self.rename_editor is not None:
            self.end_rename(refresh=False)
        proxy = view.model()
        entry = self.model.entry(proxy.mapToSource(index))
        if entry is None or entry.folder:
            return
        self.generation += 1  # discard any listing prepared before editing
        self.cancel_duplicate_view()
        self.cancel_archive_matches()
        self.cancel_size_scan()
        self.rename_view = view
        self.active_view = view
        # Follow the original visible order even if a new name sorts elsewhere.
        self.rename_queue = deque(
            e.path for row in range(index.row(), proxy.rowCount())
            if (e := self.model.entry(proxy.mapToSource(proxy.index(row,0)))) and not e.folder)
        self.next_rename()

    def next_rename(self):
        view = self.rename_view
        if view is None:
            return
        proxy = view.model()
        while self.rename_queue:
            path = self.rename_queue.popleft()
            for row in range(proxy.rowCount()):
                index = proxy.index(row,0)
                if normalized(index.data(PATH_ROLE)) != normalized(path):
                    continue
                entry = self.model.entry(proxy.mapToSource(index))
                if entry is None or entry.folder:
                    continue
                self.rename_path = entry.path
                view.setCurrentIndex(index)
                view.selectionModel().select(index, view.selectionModel().SelectionFlag.ClearAndSelect |
                                                   view.selectionModel().SelectionFlag.Rows)
                view.scrollTo(index)
                editor = RenameEdit(view.viewport())
                editor.setObjectName("inlineRename")
                editor.setStyleSheet("QLineEdit {padding: 1px 3px; border-radius: 2px;}")
                editor.setText(entry.name)
                rect = view.visualRect(index)
                editor.setGeometry(rect.adjusted(19,0,0,0) if rect.width() > 55 else rect)
                self.rename_editor = editor
                editor.commit_name.connect(lambda ed=editor: self.commit_rename(ed))
                editor.cancel_name.connect(lambda ed=editor: self.cancel_rename_editor(ed))
                editor.show()
                editor.setFocus()
                stem = Path(entry.name).stem
                editor.setSelection(0,len(stem))
                return
        self.end_rename()

    def cancel_rename_editor(self, editor):
        if self.rename_editor is editor and not self.rename_busy:
            self.end_rename()

    def remove_rename_editor(self):
        editor, self.rename_editor = self.rename_editor, None
        if editor is not None:
            editor.hide()
            editor.deleteLater()

    def end_rename(self, refresh=True):
        if self.rename_busy:
            return
        self.remove_rename_editor()
        view, self.rename_view = self.rename_view, None
        self.rename_path = None
        self.rename_queue.clear()
        if refresh and view is not None:
            self.refresh()

    def commit_rename(self, editor):
        if self.rename_editor is not editor or self.rename_busy:
            return
        path = self.rename_path
        name = editor.text()
        if not valid_name(name) or getattr(os.path,"isreserved",lambda n:False)(name):
            editor.setStyleSheet("QLineEdit {border: 1px solid #c33; padding: 1px 3px;}")
            self.statusBar().showMessage("Invalid filename; correct it or press Escape.")
            return
        if name == os.path.basename(path):
            self.remove_rename_editor()
            self.next_rename()
            return
        dest = os.path.join(os.path.dirname(path),name)
        self.rename_busy = True
        editor.setReadOnly(True)
        self.statusBar().showMessage("Renaming " + os.path.basename(path) + "...")
        def work():
            if os.path.lexists(dest) and normalized(path) != normalized(dest):
                raise FileExistsError("Already exists: " + name)
            os.rename(path,dest)
            return dest
        def done(result,error):
            self.rename_busy = False
            if error:
                if self.rename_editor is editor:
                    editor.setReadOnly(False)
                    editor.setStyleSheet("QLineEdit {border: 1px solid #c33; padding: 1px 3px;}")
                    editor.setFocus()
                self.statusBar().showMessage("Rename failed: " + error)
                return
            self.remove_rename_editor()
            for row, entry in enumerate(self.model.entries):
                if normalized(entry.path) == normalized(path):
                    self.model.entries[row] = replace(entry, path=dest, name=name,
                        archive=archive_name(name), hidden=entry.hidden or name.startswith("."))
                    old_key,new_key = normalized(path),normalized(dest)
                    for proxy in self.proxies:
                        for values in (proxy.duplicate_paths,proxy.archive_match_paths):
                            if old_key in values:
                                values.discard(old_key)
                                values.add(new_key)
                    self.model.dataChanged.emit(self.model.index(row,0),self.model.index(row,3),[])
                    break
            self.next_rename()
        self.submit(work,done)

    def preview_animation_finished(self):
        if self.preview_open:
            target = self.preview_animation.endValue()
            self.preview_panel.setMaximumWidth(16777215)
            self.size_preview_panel(target)

    def set_preview_open(self, opened):
        if self.preview_open == opened:
            return
        self.preview_open = opened
        self.preview_animation.stop()
        if opened:
            original = self.splitter.sizes()
            target = max(180,min(self.preview_width,max(180,sum(original)//2)))
            self.preview_panel.setMaximumWidth(0)
            self.preview_panel.show()
            self.preview_animation.setStartValue(0)
            self.preview_animation.setEndValue(target)
            self.preview_animation.start()
            self.schedule_preview()
        else:
            width = self.preview_panel.width()
            if width >= 100:
                self.preview_width = width
            self.stop_preview()
            self.preview_panel.hide()
            self.preview_panel.setMaximumWidth(16777215)
            self.preview_label.reset("Select an image")
            self.preview_details.clear()

    def size_preview_panel(self, target):
        sizes = self.splitter.sizes()
        available = sum(sizes)
        if available > target:
            keep = sizes[0]
            rest = max(1,sizes[1]+sizes[2])
            middle = max(0,available-target-keep)
            self.splitter.setSizes([keep,int(middle*sizes[1]/rest),
                                   middle-int(middle*sizes[1]/rest),target])

    def stop_preview(self):
        self.preview_timer.stop()
        self.preview_pending = False
        if self.preview_job is not None:
            self.preview_job.set()
            self.preview_job = None
        self.preview_key = None

    def schedule_preview(self):
        if not self.preview_open:
            return
        entries = self.selected(self.active_view) if self.active_view is not None else []
        entry = entries[0] if len(entries) == 1 else None
        key = (entry.path,entry.size,entry.modified) if entry and not entry.folder else None
        if key == self.preview_key:
            return
        self.stop_preview()
        self.preview_key = key
        self.preview_details.clear()
        if key is None:
            self.preview_label.reset("Select one image")
            return
        if Path(entry.name).suffix.casefold() not in CATEGORY_SUFFIXES["images"]:
            self.preview_label.reset("No image preview for this file")
            return
        self.preview_label.reset("Loading preview...")
        self.preview_timer.start()

    def load_preview(self):
        if not self.preview_open or self.preview_key is None:
            return
        if self.preview_inflight:
            self.preview_pending = True
            return
        key = self.preview_key
        stop = Event()
        self.preview_job = stop
        self.preview_inflight = True
        self.preview_pending = False
        def done(result,error):
            self.preview_inflight = False
            if not self.preview_open or self.preview_job is not stop or stop.is_set():
                if self.preview_open and self.preview_pending:
                    self.load_preview()
                return
            self.preview_job = None
            if error or result is None:
                self.preview_label.reset(error or "Preview unavailable")
                return
            image, original = result
            self.preview_label.set_image(image)
            self.preview_details.setText(f"{Path(key[0]).name}\n{original.width():,} × {original.height():,} px  |  {readable_size(key[1])}")
        self.submit(lambda: read_image_preview(key[0],stop.is_set),done)

    def rename(self):
        if self.editing() or not self.current:
            return
        entries = self.selected()
        if len(entries) != 1:
            return
        e = entries[0]
        if not e.folder:
            view = self.selected_view()
            self.begin_rename(view, view.selectionModel().selectedRows()[0])
            return
        name, ok = QInputDialog.getText(self, "Rename", "New name:", text=e.name)
        if not ok or name == e.name:
            return
        if not valid_name(name):
            self.warn("Invalid name")
            return
        dest = os.path.join(self.current, name)
        def work():
            if os.path.lexists(dest) and normalized(dest) != normalized(e.path):
                raise FileExistsError(dest)
            os.rename(e.path, dest)
        self.operation("Renaming", work)

    def delete(self):
        if self.editing() or not self.current:
            return
        paths = [e.path for e in self.selected()]
        if paths and QMessageBox.question(self, APP, f"Move {len(paths)} item(s) to the Recycle Bin?") == QMessageBox.Yes:
            self.operation("Recycling", lambda: recycle(paths))

    def restore_recycled(self):
        root = network_root(self.current) if self.current else None
        if root is None:
            if os.name == "nt":
                os.startfile("shell:RecycleBinFolder")
            return
        def done(records, error):
            if error:
                self.warn(error)
                return
            if not records:
                self.statusBar().showMessage("No recoverable items on this share.")
                return
            labels = [f"{date}  |  {relative}  [{key[:8]}]" for date, relative, key in records]
            label, ok = QInputDialog.getItem(self, "Restore from network recycle",
                "Select an item to restore to its original location:", labels, 0, False)
            if ok:
                key = records[labels.index(label)][2]
                self.operation("Restoring", lambda: restore_share_item(root, key))
        self.submit(lambda: share_trash_entries(root), done)

    def create_zip(self):
        if not self.current:
            return
        paths = [e.path for e in self.selected()]
        if not paths:
            return
        dest, ok = QInputDialog.getText(self, "Create ZIP", "Archive name:",
                                        text=Path(paths[0]).stem + ".zip")
        if ok:
            if not valid_name(dest):
                self.warn("Invalid archive name")
                return
            path = os.path.join(self.current, dest if dest.lower().endswith(".zip") else dest + ".zip")
            self.operation("Compressing", lambda: make_zip(paths, path))

    def do_extract(self, paths, destination, remove_source=False):
        if not paths or not destination:
            return
        self.extract_queue.extend((path, destination, remove_source) for path in paths)
        if not self.extract_running:
            self.extract_running = True
            self.next_archive()

    def next_archive(self):
        if not self.extract_queue:
            self.extract_running = False
            errors, self.extract_errors = self.extract_errors, []
            self.refresh()
            self.refresh_tree_branch()
            if errors:
                self.warn("\n".join(errors))
            return
        self.try_archive(self.extract_queue.popleft(), self.zip_password)

    def ask_zip_password(self, path, rejected):
        message = ("The previous password did not work." if rejected else "This ZIP is encrypted.")
        text, ok = QInputDialog.getText(self, "ZIP password",
            f"{Path(path).name}\n{message}\nEnter password (Cancel skips this archive):",
            QLineEdit.Password)
        return text.encode("utf-8") if ok and text else None

    def try_archive(self, item, password):
        path, destination, remove_source = item
        self.statusBar().showMessage(f"Extracting {Path(path).name}...")
        def done(result, error):
            if error:
                self.extract_errors.append(f"{Path(path).name}: {error}")
            elif result.password_required:
                replacement = self.ask_zip_password(path, password is not None)
                if replacement is not None:
                    self.try_archive(item, replacement)
                    return
                # A skipped archive is not deleted. Keep the last successful password.
                self.extract_errors.append(f"{Path(path).name}: skipped (password required)")
            else:
                if result.encrypted:
                    self.zip_password = password
                if result.error:
                    self.extract_errors.append(result.error)
            self.next_archive()
        self.submit(lambda: extract_one(path, destination, remove_source, password), done)

    def extract_zip(self):
        entries = self.selected()
        if not entries or any(e.folder or not e.name.lower().endswith(".zip") for e in entries):
            self.warn("Select ZIP archives. Other formats open in the associated application.")
            return
        self.do_extract([e.path for e in entries], self.current)

    def closeEvent(self, event):
        if self.rename_busy:
            event.ignore()
            return
        if self.duplicate_cleanup:
            self.statusBar().showMessage("Finish duplicate cleanup before closing.")
            event.ignore()
            return
        if self.extract_running:
            self.statusBar().showMessage("Finish or skip the current ZIP extraction before closing.")
            event.ignore()
            return
        self.end_rename(refresh=False)
        self.stop_preview()
        self.cancel_duplicate_view()
        self.cancel_archive_matches()
        self.cancel_size_scan()
        self.zip_password = None
        self.refresh_timer.stop()
        if self.watcher.directories():
            self.watcher.removePaths(self.watcher.directories())
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("splitter", self.splitter.saveState())
        self.settings.setValue("side_splitter", self.side_splitter.saveState())
        self.settings.setValue("preview_open", self.preview_open)
        self.settings.setValue("preview_width", self.preview_panel.width() if self.preview_open else self.preview_width)
        self.settings.setValue("location", self.current or PC)
        for i, view in enumerate(self.views):
            self.settings.setValue(f"pane/{i}/mode", self.proxies[i].mode)
            self.settings.setValue(f"pane/{i}/sort_column", view.header().sortIndicatorSection())
            self.settings.setValue(f"pane/{i}/descending", view.header().sortIndicatorOrder() == Qt.DescendingOrder)
        self.settings.sync()
        super().closeEvent(event)

def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor("#f3f5f8"))
    palette.setColor(QPalette.WindowText, QColor("#202a39"))
    palette.setColor(QPalette.Base, QColor("#ffffff"))
    palette.setColor(QPalette.Text, QColor("#202a39"))
    palette.setColor(QPalette.Button, QColor("#f3f5f8"))
    palette.setColor(QPalette.ButtonText, QColor("#202a39"))
    palette.setColor(QPalette.Highlight, QColor("#dceaff"))
    palette.setColor(QPalette.HighlightedText, QColor("#152d50"))
    app.setPalette(palette)
    app.setStyleSheet(STYLE)
    window = Explorer()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
