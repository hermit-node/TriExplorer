"""Share-local recycling, restore, Windows recycle bin routing."""
from __future__ import annotations
import os
import json
import uuid
import shutil
from pathlib import Path
from datetime import datetime
from PySide6.QtCore import QFile
from services.filesystem import valid_name

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
