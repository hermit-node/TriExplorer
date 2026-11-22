"""Exact-content duplicate detection; no filesystem mutations except caller-supplied recycle."""
from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import time
from contextlib import closing
import stat
from dataclasses import dataclass
from pathlib import Path
from collections import defaultdict

SKIP_DIRS = {".triexplorer-recycle", "$recycle.bin", "system volume information"}

@dataclass(frozen=True)
class FileRecord:
    path: str
    signature: tuple
    birth: int

    @property
    def size(self):
        return self.signature[2]

def unsafe(stat_result):
    return (stat.S_ISLNK(stat_result.st_mode)
            or bool(getattr(stat_result, "st_file_attributes", 0) & 0x400))

def record(path):
    s = os.stat(path, follow_symlinks=False)
    if unsafe(s) or not stat.S_ISREG(s.st_mode):
        raise ValueError("Not an ordinary file")
    return FileRecord(str(path), signature(s), getattr(s, "st_birthtime_ns", s.st_ctime_ns))

def signature(s):
    return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns

def hash_file(item, cancelled=lambda: False):
    if record(item.path).signature != item.signature:
        raise ValueError("File changed before hashing")
    h = hashlib.sha256()
    with open(item.path, "rb") as f:
        opened = signature(os.fstat(f.fileno()))
        # SMB and Windows path-stat vs handle-stat can report different volume
        # IDs, file IDs and timestamp representations. Compare within each API,
        # not across APIs; verify the directory entry again with the handle open.
        if opened[2] != item.size or record(item.path).signature != item.signature:
            raise ValueError("File changed before reading")
        read_bytes = 0
        while True:
            if cancelled():
                raise InterruptedError("Scan cancelled")
            chunk = f.read(4 * 1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
            read_bytes += len(chunk)
        if read_bytes != item.size or signature(os.fstat(f.fileno())) != opened:
            raise ValueError("File changed while hashing")
    if record(item.path).signature != item.signature:
        raise ValueError("File changed while hashing")
    return h.digest()

def sample_file(item, cancelled=lambda: False):
    """Reject same-size nonmatches without streaming the whole file."""
    if item.size <= 512 * 1024:
        return b"small"
    if record(item.path).signature != item.signature:
        raise ValueError("File changed before sampling")
    h = hashlib.sha256()
    with open(item.path, "rb") as f:
        opened = signature(os.fstat(f.fileno()))
        for offset in (0, (item.size - 65536)//2, item.size - 65536):
            if cancelled():
                raise InterruptedError("Scan cancelled")
            f.seek(offset)
            chunk = f.read(65536)
            if len(chunk) != 65536:
                raise ValueError("File changed while sampling")
            h.update(chunk)
        if signature(os.fstat(f.fileno())) != opened:
            raise ValueError("File changed while sampling")
    if record(item.path).signature != item.signature:
        raise ValueError("File changed while sampling")
    return h.digest()

def hash_cache(db, item, cancelled):
    if db is None:
        return hash_file(item, cancelled)
    # Cache is a browsing acceleration only. Destructive operations do not pass db.
    key = path_key(item.path)
    sig = repr(item.signature)
    try:
        Path(db).parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(db, timeout=2)) as con, con:
            con.execute("CREATE TABLE IF NOT EXISTS sha_cache (path TEXT PRIMARY KEY, signature TEXT, digest BLOB, checked REAL)")
            row = con.execute("SELECT digest FROM sha_cache WHERE path=? AND signature=? AND checked>?", (key, sig, time.time()-7*86400)).fetchone()
            if row and record(item.path).signature == item.signature:
                return row[0]
    except (OSError, sqlite3.Error):
        pass
    digest = hash_file(item, cancelled)
    try:
        with closing(sqlite3.connect(db, timeout=2)) as con, con:
            con.execute("INSERT OR REPLACE INTO sha_cache VALUES (?,?,?,?)", (key, sig, digest, time.time()))
    except sqlite3.Error:
        pass
    return digest

def path_key(path):
    return os.path.normcase(os.path.abspath(path))

def keeper_key(item):
    stem = Path(item.path).stem
    copy_name = bool(re.search(r"(?:\s*\(\d+\)|[_-]\d+|[\s_-]+copy(?:[\s_-]*\d+)?|[\s_-]+duplicate(?:[\s_-]*\d+)?)$", stem, re.I))
    return copy_name, item.birth, path_key(item.path)

@dataclass
class ScanResult:
    groups: list
    errors: list

def find_duplicates(root, recursive=False, preferred=None, show_hidden=False, cancelled=lambda: False,
                    progress=lambda done, total: None, matches=lambda groups, done, total: None, cache_db=None):
    errors = []
    buckets = defaultdict(list)
    selected = record(preferred) if preferred else None
    stack = [os.path.abspath(root)]
    while stack and not cancelled():
        folder = stack.pop()
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    if cancelled():
                        break
                    name = entry.name.casefold()
                    if name in SKIP_DIRS or name.startswith(".triextract-"):
                        continue
                    try:
                        s = entry.stat(follow_symlinks=False)
                        if unsafe(s):
                            continue
                        hidden = name.startswith(".") or bool(getattr(s, "st_file_attributes", 0) & 6)
                        if hidden and not show_hidden and path_key(entry.path) != path_key(preferred or root):
                            continue
                        if stat.S_ISDIR(s.st_mode):
                            if recursive:
                                stack.append(entry.path)
                            continue
                        if not stat.S_ISREG(s.st_mode):
                            continue
                        if selected is not None and s.st_size != selected.size:
                            continue
                        buckets[s.st_size].append(entry.path)
                    except OSError as exc:
                        errors.append(f"{entry.path}: {exc}")
        except OSError as exc:
            errors.append(f"{folder}: {exc}")
    groups = []
    total = sum(len(items) for items in buckets.values() if len(items) > 1)
    completed = 0
    progress(completed, total)
    # Small groups first: a multi-gigabyte archive must not delay reporting small matches.
    for _, items in sorted(buckets.items()):
        if cancelled():
            break
        if len(items) < 2:
            continue
        hashes = defaultdict(list)
        samples = defaultdict(list)
        for path in items:
            if cancelled():
                return ScanResult([], [])
            try:
                item = record(path)
                samples[sample_file(item, cancelled)].append(item)
            except InterruptedError:
                return ScanResult([], [])
            except (OSError, ValueError) as exc:
                errors.append(f"{path}: {exc}")
                completed += 1
        for candidates in samples.values():
            if len(candidates) < 2:
                completed += len(candidates)
                continue
            for item in candidates:
                if cancelled():
                    return ScanResult([], [])
                try:
                    hashes[hash_cache(cache_db, item, cancelled)].append(item)
                except InterruptedError:
                    return ScanResult([], [])
                except (OSError, ValueError) as exc:
                    errors.append(f"{item.path}: {exc}")
                completed += 1
                if completed == total or completed % 8 == 0:
                    progress(completed, total)
        progress(completed, total)
        for same in hashes.values():
            if len(same) < 2:
                continue
            if selected:
                keep = next((item for item in same if path_key(item.path) == path_key(selected.path)
                             and item.signature == selected.signature), None)
                if keep is None:
                    continue
            else:
                keep = min(same, key=keeper_key)
            group = (keep, [item for item in same if item != keep])
            groups.append(group)
            matches([group], completed, total)
    return ScanResult(groups, errors)

def remove_duplicates(result, recycle, cancelled=lambda: False):
    errors = list(result.errors)
    removed = 0
    for keep, copies in result.groups:
        for copy in copies:
            if cancelled():
                return removed, errors
            try:
                # Fail closed if either path's identity or metadata changed after hashing.
                if record(keep.path).signature != keep.signature:
                    errors.append(f"Keeper changed; remaining copies retained: {keep.path}")
                    break
                if record(copy.path).signature != copy.signature:
                    errors.append(f"Duplicate changed; retained: {copy.path}")
                    continue
                failures = recycle([copy.path])
                if failures:
                    errors.extend(failures)
                else:
                    removed += 1
            except (OSError, ValueError) as exc:
                errors.append(f"{copy.path}: {exc}")
    return removed, errors
