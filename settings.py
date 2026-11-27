"""Portable settings.ini authority and optional legacy registry migration."""
from __future__ import annotations
import os
from pathlib import Path
from PySide6.QtCore import QSettings
import folder_sizes
import runtime_paths
import duplicates
from services.filesystem import valid_name
from services import recycle as recycle_store

APP = "TriExplorer"
def settings_file() -> Path:
    return runtime_paths.application_directory() / "settings.ini"


# Compatibility reference for source callers and existing tests.
SETTINGS_PATH = settings_file()

DEFAULT_CONFIG = {
    "app/name": "TriExplorer",
    "cache/sqlite_path": "auto",
    "watcher/refresh_ms": 1500,
    "network/trash_name": ".TriExplorer-Recycle",
    "hidden": False,
    "preview_open": False,
    "preview_width": 340,
    "pane/0/mode": "default",
    "pane/0/sort_column": 0,
    "pane/0/descending": False,
    "pane/1/mode": "archives",
    "pane/1/sort_column": 0,
    "pane/1/descending": False,
}

def open_settings():
    """Load portable settings.ini; import any missing legacy registry preferences once."""
    settings = QSettings(str(settings_file()), QSettings.IniFormat)
    if not settings.value("meta/legacy_imported", False, type=bool):
        if not runtime_paths.is_frozen():
            legacy = QSettings("KHzz", APP)
            for key in legacy.allKeys():
                if not settings.contains(key):
                    settings.setValue(key, legacy.value(key))
        settings.setValue("meta/legacy_imported", True)
    for key, value in DEFAULT_CONFIG.items():
        if not settings.contains(key):
            settings.setValue(key, value)
    settings.sync()
    return settings

def configured_cache_path(settings):
    """Frozen auto cache is beside the EXE; source auto retains existing cache."""
    raw = str(settings.value("cache/sqlite_path", "auto", type=str)).strip()
    if not raw or raw.casefold() == "auto":
        if runtime_paths.is_frozen():
            return settings_file().with_name("TriExplorer.sqlite3")
        return folder_sizes.cache_path()
    expanded = Path(os.path.expandvars(os.path.expanduser(raw)))
    if not expanded.is_absolute():
        expanded = settings_file().parent / expanded
    return expanded.resolve()

def configure_network_trash(settings):
    """Recycle directory is a safe single name under each SMB share's root."""
    name = str(settings.value("network/trash_name", recycle_store.NETWORK_TRASH, type=str)).strip()
    if (not valid_name(name) or name in (".", "..")
            or "/" in name or chr(92) in name
            or getattr(os.path, "isreserved", lambda _: False)(name)):
        raise ValueError("network/trash_name must be a single safe directory name")
    recycle_store.NETWORK_TRASH = name
    folder_sizes.EXCLUDED.add(name.casefold())
    duplicates.SKIP_DIRS.add(name.casefold())
