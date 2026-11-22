"""Read-only ZIP versus directory equivalence checks."""
from __future__ import annotations
import hashlib
import os
import stat
import zipfile
from pathlib import Path
from dataclasses import dataclass
import duplicates
import folder_sizes

@dataclass(frozen=True)
class Match:
    archive: str
    folder: str
    status: str
    verified: bool
    encrypted: bool = False
    password_required: bool = False

def manifest(folder, cancelled):
    root = os.stat(folder, follow_symlinks=False)
    if duplicates.unsafe(root) or not stat.S_ISDIR(root.st_mode):
        raise ValueError("Not an ordinary folder")
    files = {}
    directories = set()
    todo = [(str(folder), ())]
    while todo:
        path, parts = todo.pop()
        with os.scandir(path) as entries:
            for e in entries:
                if cancelled():
                    raise InterruptedError()
                s = e.stat(follow_symlinks=False)
                if duplicates.unsafe(s):
                    raise ValueError("Folder contains a link/reparse point")
                rel = parts + (e.name.casefold(),)
                if stat.S_ISDIR(s.st_mode):
                    if rel in directories:
                        raise ValueError("Case-colliding directory names")
                    directories.add(rel)
                    todo.append((e.path, rel))
                elif stat.S_ISREG(s.st_mode):
                    if rel in files:
                        raise ValueError("Case-colliding file names")
                    files[rel] = duplicates.record(e.path)
                else:
                    raise ValueError("Folder contains a non-regular file")
    return files, directories

def archive_manifest(members, strip):
    files = {}
    dirs = set()
    for m, parts, is_dir in members:
        rel = tuple(p.casefold() for p in parts[1 if strip else 0:])
        if not rel:
            continue
        for i in range(1, len(rel)):
            dirs.add(rel[:i])
        if is_dir:
            dirs.add(rel)
        else:
            files[rel] = m
    return files, dirs

def compare(archive, folder, validator, password=None, cancelled=lambda: False,
            folder_manifest=None):
    original = duplicates.record(archive)
    with zipfile.ZipFile(archive) as probe:
        aes = any(m.compress_type == 99 for m in probe.infolist())
    cls = zipfile.ZipFile
    if aes:
        try:
            from pyzipper import AESZipFile
            cls = AESZipFile
        except ImportError:
            # Central directory remains readable but cannot verify encrypted bytes.
            cls = zipfile.ZipFile
    files, dirs = folder_manifest if folder_manifest is not None else manifest(folder, cancelled)
    with cls(archive) as z:
        members = validator(z)
        if not members or not files:
            return None
        wrapped = len({parts[0].casefold() for _, parts, _ in members}) == 1 and all(
            len(parts)>1 or is_dir for _, parts, is_dir in members)
        chosen = None
        for strip in (False, True) if wrapped else (False,):
            entries, entry_dirs = archive_manifest(members, strip)
            if set(entries) != set(files) or entry_dirs != dirs:
                continue
            if any(entries[k].file_size != f.size for k,f in files.items()):
                continue
            chosen = entries
            break
        if chosen is None:
            return None
        encrypted = any(m.flag_bits & 1 for m in chosen.values())
        if encrypted and aes and cls is zipfile.ZipFile:
            return Match(str(archive), str(folder), "Same paths and sizes; AES support unavailable (install pyzipper)", False, True)
        if encrypted and not password:
            return Match(str(archive), str(folder), "Same paths and sizes; password needed for SHA verification", False, True, True)
        for k, m in sorted(chosen.items(), key=lambda x: x[1].file_size):
            if cancelled():
                raise InterruptedError()
            h = hashlib.sha256()
            n = 0
            try:
                with z.open(m, pwd=password if m.flag_bits & 1 else None) as src:
                    while True:
                        if cancelled():
                            raise InterruptedError()
                        data = src.read(4*1024*1024)
                        if not data:
                            break
                        n += len(data)
                        h.update(data)
            except RuntimeError as exc:
                if encrypted and "password" in str(exc).lower():
                    return Match(str(archive), str(folder), "Same paths and sizes; ZIP password rejected", False, True, True)
                raise
            if n != files[k].size or duplicates.hash_file(files[k], cancelled) != h.digest():
                return None
    if duplicates.record(archive).signature != original.signature:
        raise ValueError("ZIP changed during verification")
    # Re-enumerate after comparison, including extra files and directories.
    if manifest(folder, cancelled) != (files, dirs):
        raise ValueError("Folder changed during verification")
    return Match(str(archive), str(folder), "Verified: every file SHA-256 matches", True, encrypted)

def find_matches(folders, archives, sizes, db, validator, password=None,
                 cancelled=lambda: False, progress=lambda n,total,m: None):
    """Only open candidate ZIPs; populate missing folder-size cache once."""
    errors = []
    matches = []
    known = dict(sizes)
    missing = [f for f in folders if folder_sizes.key(f) not in known]
    if missing:
        def sized(path, value, error):
            if value is not None:
                known[path] = value
        errors.extend(folder_sizes.refresh(db, missing, cancelled, sized))
    candidates = []
    for path, compressed_size in archives:
        if cancelled():
            return matches, errors
        stem = Path(path).stem.casefold()
        for folder in folders:
            size = known.get(folder_sizes.key(folder))
            # Name-based fallback for strongly compressed or uncached folders.
            named = Path(folder).name.casefold() == stem
            if named or (size is not None and size.size > 0
                         and 0.8*size.size <= compressed_size <= size.size + min(4096, max(256, size.size*0.02))):
                candidates.append((path, folder))
    total = len(candidates)
    progress(0,total,None)
    manifests = {}
    zip_totals = {}
    for n,(archive, folder) in enumerate(candidates,1):
        if cancelled():
            break
        match = None
        try:
            # Exact uncompressed total is a cheap additional rejection.
            if archive not in zip_totals:
                with zipfile.ZipFile(archive) as z:
                    zip_totals[archive] = sum(m.file_size for m in z.infolist() if not m.is_dir())
            size = zip_totals[archive]
            cached = known.get(folder_sizes.key(folder))
            if cached is not None and not cached.stale and cached.size != size and Path(archive).stem.casefold() != Path(folder).name.casefold():
                continue
            if folder not in manifests:
                manifests[folder] = manifest(folder, cancelled)
            match = compare(archive,folder,validator,password,cancelled,manifests[folder])
            if match:
                matches.append(match)
        except InterruptedError:
            break
        except (OSError,ValueError,RuntimeError,zipfile.BadZipFile,NotImplementedError) as exc:
            errors.append(f"{Path(archive).name} / {Path(folder).name}: {exc}")
        finally:
            progress(n,total,match)
    return matches,errors
