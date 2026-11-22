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
                            QObject, Signal, QRunnable, QThreadPool, QStandardPaths, QEvent, QSize, QPropertyAnimation,
                            QEasingCurve)
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence, QDrag, QPalette, QColor, QIcon, QPixmap, QPainter, \
    QImageReader
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                               QHBoxLayout, QSplitter, QTreeWidget, QTreeWidgetItem, QTreeView, QHeaderView,
                               QAbstractItemView, QToolButton, QLineEdit, QLabel, QMenu, QMessageBox,
                               QInputDialog, QStyle, QFrame, QFileDialog, QComboBox, QSizePolicy)

import stylesheet
from filters import PANE_MODES, CATEGORY_SUFFIXES, ARCHIVE_SUFFIXES, archive_name
from panels import (
    PC, PATH_ROLE, SORT_ROLE, LOADED_ROLE, Entry, normalized, readable_size,
    Files, PaneFilter, RenameEdit, PreviewLabel, FileView, read_image_preview,
    create_table_pane,
)

from settings import APP, SETTINGS_PATH, DEFAULT_CONFIG, open_settings, configured_cache_path
from settings import configure_network_trash as _configure_network_trash
from workers import WorkerSignals, Worker
from services.filesystem import scan, valid_name, reject_junctions, transfer
from services import recycle as _trash_engine
from services.recycle import (
    network_root, trash_directory, checked_trash, share_recycle,
    share_trash_entries, restore_share_item, recycle,
)
from services.archive import (
    make_zip, zip_members, zip_root, ZipPasswordRequired, open_zip_member,
    extract_members, unzip, smart_unzip, archive_signature, ExtractionResult,
    extract_one, extract_archives,
)

NETWORK_TRASH = _trash_engine.NETWORK_TRASH


def configure_network_trash(settings):
    """Keep the exported compatibility name synchronized with recycle engine state."""
    _configure_network_trash(settings)
    global NETWORK_TRASH
    NETWORK_TRASH = _trash_engine.NETWORK_TRASH


def svg_icon(body):
    pixel = QPixmap(48, 48)
    pixel.fill(Qt.transparent)
    renderer = QSvgRenderer(('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">' + body + '</svg>').encode())
    painter = QPainter(pixel)
    renderer.render(painter)
    painter.end()
    return QIcon(pixel)
