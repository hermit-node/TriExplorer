"""Persistent, per-machine folder size cache. Scans metadata, not file contents."""
from __future__ import annotations
import os
import sqlite3
import stat
import time
from pathlib import Path
from dataclasses import dataclass
from contextlib import closing

MAX_AGE = 7 * 24 * 60 * 60
EXCLUDED = {".triexplorer-recycle", "$recycle.bin", "system volume information"}

@dataclass(frozen=True)
class FolderSize:
    size: int
    checked: float
    modified: float
    stale: bool = False

def cache_path():
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "TriExplorer" / "folder-sizes.sqlite3"

def key(path):
    return os.path.normcase(os.path.abspath(path))

def connect(db):
    db = Path(db)
    db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db, timeout=10)
    con.execute("CREATE TABLE IF NOT EXISTS folder_sizes (path TEXT PRIMARY KEY, bytes INTEGER NOT NULL, checked REAL NOT NULL, modified REAL NOT NULL)")
    return con

def lookup(db, folders):
    if not folders:
        return {}
    result = {}
    now = time.time()
    with closing(connect(db)) as con, con:
        for path, modified in folders:
            k = key(path)
            row = con.execute("SELECT bytes, checked, modified FROM folder_sizes WHERE path=?", (k,)).fetchone()
            if row:
                size, checked, old_modified = row
                result[k] = FolderSize(size, checked, old_modified,
                    now - checked > MAX_AGE or modified is None or abs(modified - old_modified) > 0.00001)
    return result

def is_link(s):
    return stat.S_ISLNK(s.st_mode) or bool(getattr(s, "st_file_attributes", 0) & 0x400)

def scan_tree(path, cancelled=lambda: False):
    """Return total and per-descendant totals from a single iterative postorder traversal.
    Do not publish incomplete totals on I/O errors or cancellation.
    """
    root = os.path.abspath(path)
    stack = [(root, False, None)]
    totals = {}
    records = {}
    checked = time.time()
    while stack:
        if cancelled():
            raise InterruptedError("Folder size scan cancelled")
        folder, complete, info = stack.pop()
        if complete:
            own, children, modified = info
            size = own + sum(totals[child] for child in children)
            if os.stat(folder, follow_symlinks=False).st_mtime != modified:
                raise OSError("Directory changed during size calculation")
            totals[folder] = size
            records[key(folder)] = (size, checked, modified)
            continue
        s = os.stat(folder, follow_symlinks=False)
        if is_link(s) or not stat.S_ISDIR(s.st_mode):
            raise ValueError("Not an ordinary directory")
        own, children = 0, []
        with os.scandir(folder) as entries:
            for item in entries:
                if cancelled():
                    raise InterruptedError("Folder size scan cancelled")
                name = item.name.casefold()
                if name in EXCLUDED or name.startswith(".triextract-"):
                    continue
                child = item.stat(follow_symlinks=False)
                if is_link(child):
                    continue
                if stat.S_ISDIR(child.st_mode):
                    children.append(item.path)
                elif stat.S_ISREG(child.st_mode):
                    own += child.st_size
        stack.append((folder, True, (own, children, s.st_mtime)))
        stack.extend((child, False, None) for child in reversed(children))
    return records

def refresh(db, paths, cancelled=lambda: False, progress=lambda path, value, error: None):
    errors = []
    for path in paths:
        if cancelled():
            break
        try:
            records = scan_tree(path, cancelled)
            if cancelled():
                break
            with closing(connect(db)) as con, con:
                con.executemany("INSERT INTO folder_sizes VALUES (?,?,?,?) ON CONFLICT(path) DO UPDATE SET bytes=excluded.bytes, checked=excluded.checked, modified=excluded.modified",
                                [(k, *value) for k, value in records.items()])
            value = FolderSize(*records[key(path)])
            progress(key(path), value, None)
        except InterruptedError:
            break
        except (OSError, ValueError, sqlite3.Error) as exc:
            errors.append(f"{path}: {exc}")
            progress(key(path), None, str(exc))
    return errors
