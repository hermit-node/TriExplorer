"""ZIP creation, validation, extraction and worker-compatible batch helpers."""
from __future__ import annotations
import os
import shutil
import tempfile
import zipfile
from pathlib import Path
from dataclasses import dataclass
from file_types import normalized
from services.filesystem import valid_name
from services.recycle import recycle

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
