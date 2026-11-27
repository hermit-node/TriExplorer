"""Isolated diagnostic entry point; never schedules automatic directory refreshes."""
from __future__ import annotations
import argparse
import logging
import logging.handlers
import queue
import shutil
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

from PySide6.QtCore import QSettings, QTimer
from PySide6.QtWidgets import QApplication
from explorer import Explorer
from settings import DEFAULT_CONFIG, settings_file, APP
import app_icons
import stylesheet

LOG = logging.getLogger("triexplorer.debug")

def isolated_settings(directory, source, folder=None):
    target = directory / "settings.ini"
    if source.is_file():
        shutil.copyfile(source, target)
    settings = QSettings(str(target), QSettings.IniFormat)
    for key, value in DEFAULT_CONFIG.items():
        if not settings.contains(key):
            settings.setValue(key, value)
    settings.setValue("meta/legacy_imported", True)
    settings.setValue("watcher/enabled", False)
    settings.setValue("watcher/network_enabled", False)
    settings.setValue("cache/sqlite_path", str(directory / "debug.sqlite3"))
    if folder is not None:
        settings.setValue("location", folder)
    settings.sync()
    return settings

def caller():
    return " <- ".join(f"{Path(f.filename).name}:{f.lineno}:{f.name}"
                       for f in reversed(traceback.extract_stack(limit=8)[:-2]))

class DebugExplorer(Explorer):
    def __init__(self, settings):
        self.last_beat = time.monotonic()
        self.stop_monitor = threading.Event()
        self.gui_thread = threading.get_ident()
        super().__init__(settings=settings)
        self.setWindowTitle(self.windowTitle() + " [DEBUG - auto refresh OFF]")
        self.statusBar().messageChanged.connect(lambda msg: LOG.info("STATUS %s", msg))
        self.model.modelReset.connect(lambda: LOG.info("MODEL reset rows=%d", self.model.rowCount()))
        self.model.rowsInserted.connect(lambda _p, a, b: LOG.info("MODEL inserted %d", b-a+1))
        self.model.rowsRemoved.connect(lambda _p, a, b: LOG.info("MODEL removed %d", b-a+1))
        self.heartbeat = QTimer(self, interval=250)
        self.heartbeat.timeout.connect(self.beat)
        self.beat()
        self.heartbeat.start()
        threading.Thread(target=self.monitor_ui, daemon=True, name="ui-watchdog").start()

    def should_watch_folder(self, remote):
        # Register even for SMB, but our callback only logs, never starts a timer.
        return True

    def queue_watcher_refresh(self, path):
        LOG.info("WATCH notification path=%r current=%r action=ignored", path, self.current)

    def refresh_from_watcher(self):
        LOG.warning("WATCH timer callback blocked path=%r", self.current)
        self.refresh_timer.stop()

    def navigate(self, path, record=True):
        LOG.info("NAVIGATE path=%r record=%s source=%s", path, record, caller())
        return super().navigate(path, record)

    def load_folder(self, clear=False, from_watcher=False):
        LOG.info("LOAD request path=%r clear=%s watcher=%s generation=%s source=%s",
                 self.current, clear, from_watcher, self.generation, caller())
        if from_watcher:
            LOG.warning("LOAD auto refresh blocked")
            return
        return super().load_folder(clear, from_watcher)

    def submit(self, fn, done, progress=None):
        name = getattr(fn, "__qualname__", type(fn).__name__)
        queued = time.monotonic()
        path = self.current
        LOG.info("WORK queued task=%s folder=%r pending=%d", name, path, len(self.workers))
        def work(*args):
            start = time.monotonic()
            LOG.info("WORK start task=%s wait_ms=%.1f", name, (start-queued)*1000)
            try:
                result = fn(*args)
                if name.endswith("list_with_sizes"):
                    entries, sizes, remote = result
                    LOG.info("SCAN entries=%d cached=%d remote=%s folder=%r",
                             len(entries), len(sizes), remote, path)
                return result
            finally:
                LOG.info("WORK end task=%s duration_ms=%.1f", name, (time.monotonic()-start)*1000)
        def finished(result, error):
            start = time.monotonic()
            try:
                return done(result, error)
            finally:
                LOG.info("WORK callback task=%s duration_ms=%.1f error=%r rows=%d watched=%r",
                         name, (time.monotonic()-start)*1000, error, self.model.rowCount(),
                         self.watcher.directories())
        return super().submit(work, finished, progress)

    def beat(self):
        now = time.monotonic()
        elapsed = now-self.last_beat
        self.last_beat = now
        if elapsed > .5:
            LOG.warning("UI heartbeat delayed %.0f ms", elapsed*1000)

    def monitor_ui(self):
        reported = False
        while not self.stop_monitor.wait(.5):
            elapsed = time.monotonic()-self.last_beat
            if elapsed > 1.5 and not reported:
                frame = sys._current_frames().get(self.gui_thread)
                stack = traceback.format_stack(frame) if frame else []
                LOG.warning("UI STALL %.0f ms\n%s", elapsed*1000, "".join(stack))
                reported = True
            elif elapsed < .5:
                reported = False

    def closeEvent(self, event):
        super().closeEvent(event)
        if event.isAccepted():
            self.stop_monitor.set()
            self.heartbeat.stop()
            LOG.info("SESSION closing")

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--settings", type=Path, default=settings_file(),
                        help="Read a copy of this settings.ini; original is untouched")
    parser.add_argument("--folder", help="Start in this directory")
    args = parser.parse_args()
    directory = Path(tempfile.mkdtemp(prefix="TriExplorer-debug-"))
    logfile = directory / "debug.log"
    handler = logging.handlers.RotatingFileHandler(logfile, maxBytes=5*1024*1024,
                                                    backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(threadName)s] %(message)s"))
    messages = queue.Queue()
    queue_handler = logging.handlers.QueueHandler(messages)
    LOG.addHandler(queue_handler)
    LOG.setLevel(logging.INFO)
    listener = logging.handlers.QueueListener(messages, handler)
    listener.start()
    print(f"Debug log: {logfile}", flush=True)
    try:
        LOG.info("SESSION start auto_refresh=off settings_source=%s settings_copy=%s",
                 args.settings, directory / "settings.ini")
        app_icons.register_taskbar_identity()
        app = QApplication([sys.argv[0]])
        app.setApplicationName(APP)
        app.setOrganizationName("KHzz")
        app.setWindowIcon(app_icons.app_icon())
        app.setStyle("Fusion")
        app.setPalette(stylesheet.import_palette())
        app.setStyleSheet(stylesheet.STYLE)
        window = DebugExplorer(isolated_settings(directory, args.settings, args.folder))
        window.show()
        return app.exec()
    finally:
        listener.stop()
        LOG.removeHandler(queue_handler)
        handler.close()
