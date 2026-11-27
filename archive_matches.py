"""Read-only ZIP/ISO directory equivalence and containment checks."""
from __future__ import annotations
import hashlib
import os
import stat
import zipfile
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from dataclasses import dataclass
import duplicates

@dataclass(frozen=True)
class Match:
    archive: str
    folder: str
    status: str
    verified: bool
    encrypted: bool = False
    password_required: bool = False
    contained: bool = False
    recyclable: bool = True

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
                if rel in files or rel in directories:
                    raise ValueError("Case-colliding names")
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
            if rel in files:
                raise ValueError("Case-colliding archive member")
            files[rel] = m
    if dirs.intersection(files):
        raise ValueError("Archive file/directory conflict")
    return files, dirs

@contextmanager
def open_archive(archive, validator, cancelled):
    if Path(archive).suffix.casefold() == ".iso":
        from services.iso_reader import open_iso
        with open_iso(archive, cancelled) as result:
            yield result
        return
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
    with cls(archive) as z:
        yield z, validator(z), aes and cls is zipfile.ZipFile, False


def layouts(members):
    wrapped = bool(members) and len({parts[0].casefold() for _, parts, _ in members}) == 1 and all(
        len(parts)>1 or is_dir for _, parts, is_dir in members)
    return [archive_manifest(members, strip) for strip in ((False, True) if wrapped else (False,))]


def size_screen(archive_sizes, folder_sizes):
    total = sum(archive_sizes.values())
    return total > 0 and sum((archive_sizes & folder_sizes).values()) * 100 >= 70 * total


def choose_layout(variants, files, dirs, allow_contained):
    for contained in ((False, True) if allow_contained else (False,)):
        for entries, entry_dirs in variants:
            if not entries:
                continue
            if contained:
                if not entry_dirs.issubset(dirs) or not entries.keys() <= files.keys():
                    continue
            elif entry_dirs != dirs or entries.keys() != files.keys():
                continue
            if any(files[k].size != m.file_size for k,m in entries.items()):
                continue
            return entries, contained
    return None, False


def compare(archive, folder, validator, password=None, cancelled=lambda: False,
            folder_manifest=None, allow_contained=False):
    original = duplicates.record(archive)
    files, dirs = folder_manifest if folder_manifest is not None else manifest(folder, cancelled)
    with open_archive(archive, validator, cancelled) as (z, members, aes_missing, protected):
        chosen, contained = choose_layout(layouts(members), files, dirs, allow_contained)
        if chosen is None:
            return None
        prefix = "Contained paths and sizes" if contained else "Same paths and sizes"
        encrypted = any(m.flag_bits & 1 for m in chosen.values())
        if encrypted and aes_missing:
            return Match(str(archive), str(folder), prefix + "; AES support unavailable (install pyzipper)", False, True, contained=contained)
        if encrypted and not password:
            return Match(str(archive), str(folder), prefix + "; password needed for SHA verification", False, True, True, contained)
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
                    return Match(str(archive), str(folder), prefix + "; ZIP password rejected", False, True, True, contained)
                raise
            if n != files[k].size or duplicates.hash_file(files[k], cancelled) != h.digest():
                return None
    if duplicates.record(archive).signature != original.signature:
        raise ValueError("Archive changed during verification")
    # Re-enumerate after comparison, including extra files and directories.
    if manifest(folder, cancelled) != (files, dirs):
        raise ValueError("Folder changed during verification")
    status = ("Contained: every archive file SHA-256 matches; folder has extras" if contained
              else "Verified: every file SHA-256 matches")
    if protected:
        status += "; ISO boot/system data or alternate filesystem may be unique; retain image"
    return Match(str(archive), str(folder), status, True, encrypted, contained=contained, recyclable=not protected)

def find_matches(folders, archives, sizes, db, validator, password=None,
                 cancelled=lambda: False, progress=lambda n,total,m: None,
                 allow_contained=False):
    """Use actual uncompressed sizes, not cached total or compression ratio."""
    errors, matches, manifests, counters = [], [], {}, {}
    candidates = []
    progress(0, 0, None)
    for folder in folders:
        if cancelled():
            return matches, errors
        try:
            files, dirs = manifest(folder, cancelled)
            manifests[folder] = files, dirs
            counters[folder] = Counter(f.size for f in files.values())
        except InterruptedError:
            return matches, errors
        except (OSError, ValueError) as exc:
            errors.append(f"{Path(folder).name}: {exc}")
    for archive, _ in archives:
        if cancelled():
            return matches, errors
        try:
            with open_archive(archive, validator, cancelled) as (_, members, _, _):
                variants = layouts(members)
                lengths = Counter(m.file_size for m, _, d in members if not d)
            for folder, (files, dirs) in manifests.items():
                if cancelled():
                    return matches, errors
                if not size_screen(lengths, counters[folder]):
                    continue
                if not allow_contained and lengths != counters[folder]:
                    continue
                chosen, _ = choose_layout(variants, files, dirs, allow_contained)
                if chosen is not None:
                    candidates.append((archive, folder))
        except InterruptedError:
            return matches, errors
        except Exception as exc:
            errors.append(f"{Path(archive).name}: {exc}")
    total = len(candidates)
    progress(0, total, None)
    for n, (archive, folder) in enumerate(candidates, 1):
        if cancelled():
            break
        match = None
        try:
            match = compare(archive, folder, validator, password, cancelled,
                            manifests[folder], allow_contained=allow_contained)
            if match:
                matches.append(match)
        except InterruptedError:
            break
        except Exception as exc:
            errors.append(f"{Path(archive).name} / {Path(folder).name}: {exc}")
        finally:
            progress(n,total,match)
    return matches, errors
