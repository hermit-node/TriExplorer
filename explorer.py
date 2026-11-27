"""TriExplorer window: shared state and controller composition.

Feature methods live in controllers/; filesystem helpers in appcore.py.
"""
from __future__ import annotations

from appcore import *  # compatibility export for existing tools and tests
from controllers.duplicate_actions import DuplicateActionsMixin
from controllers.sidebar import SidebarMixin
from controllers.navigation import NavigationMixin
from controllers.selection import SelectionMixin
from controllers.file_actions import FileActionsMixin
from controllers.preview import PreviewMixin
from controllers.archive_actions import ArchiveActionsMixin
from controllers.layout import LayoutMixin
import app_icons


class Explorer(DuplicateActionsMixin, SidebarMixin, NavigationMixin, SelectionMixin, FileActionsMixin, PreviewMixin, ArchiveActionsMixin, LayoutMixin, QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = open_settings()
        configure_network_trash(self.settings)
        self.app_name = str(self.settings.value("app/name", APP, type=str)).strip() or APP
        self.setWindowTitle(self.app_name)
        self.setWindowIcon(app_icons.app_icon())
        self.resize(1450, 820)
        self.setMinimumSize(850, 480)
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
        self.size_db = configured_cache_path(self.settings)
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
            "folder": svg_icon(
                '<path d="M2 6V4a1 1 0 0 1 1-1h6l2 3h10a1 1 0 0 1 1 1v13H2z" fill="#e9b656" stroke="#b98531" stroke-linejoin="round"/><path d="M2 8h20v12H2z" fill="#f4ce7d" stroke="#b98531" stroke-linejoin="round"/>'),
            "file": svg_icon(
                '<path d="M5 2h9l5 5v15H5z" fill="#f3f6fa" stroke="#8a9db4" stroke-width="1.4" stroke-linejoin="round"/><path d="M14 2v5h5M8 12h8M8 15h8M8 18h5" fill="none" stroke="#8a9db4" stroke-linecap="round"/>'),
            "archive": svg_icon(
                '<rect x="3" y="2" width="18" height="20" rx="2" fill="#e3eaf9" stroke="#728dbd" stroke-width="1.3"/><path d="M12 3v12" stroke="#526d9e" stroke-width="2" stroke-dasharray="2 1"/><rect x="9.5" y="14" width="5" height="5" rx="1" fill="#fff" stroke="#526d9e"/>')
        })
        self.model = Files(self.icons, self)
        self.watcher = QFileSystemWatcher(self)
        self.folder_load_token = None
        self.watcher_enabled = self.settings.value("watcher/enabled", True, type=bool)
        self.network_watcher_enabled = self.settings.value("watcher/network_enabled", False, type=bool)
        self.current_is_network = False
        try:
            refresh_ms = int(self.settings.value("watcher/refresh_ms", 1500))
        except (TypeError, ValueError):
            refresh_ms = 1500
        self.refresh_timer = QTimer(self, singleShot=True, interval=max(500, min(30000, refresh_ms)))
        self.refresh_timer.timeout.connect(self.refresh_from_watcher)
        self.watcher.directoryChanged.connect(self.queue_watcher_refresh)
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
