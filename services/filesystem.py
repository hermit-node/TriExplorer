"""OS directory enumeration, safe file transfers and name validation."""
from __future__ import annotations
import os
import shutil
from pathlib import Path
from PySide6.QtCore import QDir
from file_types import Entry, PC, normalized
from filters import archive_name

def scan(path):
    from services import recycle as trash_service
    if path == PC:
        return [Entry(os.path.normpath(d.absoluteFilePath()), d.absoluteFilePath(), True)
                for d in QDir.drives()]
    entries = []
    with os.scandir(path) as it:
        for item in it:
            if item.name.casefold() == trash_service.NETWORK_TRASH.casefold():
                continue
            try:
                folder = item.is_dir()
                stat = item.stat(follow_symlinks=False)
                hidden = item.name.startswith(".") or bool(getattr(stat, "st_file_attributes", 0) & 2)
                entries.append(Entry(item.path, item.name, folder,
                                     not folder and archive_name(item.name),
                                     None if folder else stat.st_size, stat.st_mtime, hidden))
            except OSError:
                # A file may disappear or deny metadata access during enumeration.
                try:
                    folder = item.is_dir()
                except OSError:
                    folder = False
                entries.append(Entry(item.path, item.name, folder,
                                     not folder and archive_name(item.name), hidden=item.name.startswith(".")))
    return entries


def valid_name(name):
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                *(f"LPT{i}" for i in range(1, 10))}
    if os.name == "nt" and name.split(".")[0].strip().upper() in reserved:
        return False
    return bool(name and name not in (".", "..") and not any(c in name for c in '/\\<>:"|?*')
                and not name.endswith((" ", ".")) and not any(ord(c) < 32 for c in name))


def reject_junctions(path):
    if getattr(os.path, "isjunction", lambda _: False)(path):
        raise ValueError("Copying a junction is not supported")
    if os.path.isdir(path) and not os.path.islink(path):
        for base, dirs, _files in os.walk(path, followlinks=False):
            for name in dirs:
                if getattr(os.path, "isjunction", lambda _: False)(os.path.join(base, name)):
                    raise ValueError(f"Folder contains a junction: {os.path.join(base, name)}")


def transfer(paths, target, move):
    errors = []
    for source in paths:
        try:
            source = os.path.abspath(source)
            if os.path.dirname(source) == source:
                raise ValueError("Cannot transfer a drive root")
            dest = os.path.join(target, os.path.basename(source))
            if normalized(source) == normalized(dest):
                raise ValueError("Source and destination are the same")
            if os.path.lexists(dest):
                raise FileExistsError(f"Already exists: {dest}")
            if os.path.isdir(source):
                real_src, real_dest = os.path.realpath(source), os.path.realpath(dest)
                try:
                    child = normalized(os.path.commonpath([real_src, real_dest])) == normalized(real_src)
                except ValueError:
                    child = False
                if child:
                    raise ValueError("Cannot copy or move a folder inside itself")
            if move:
                # Windows rename does not replace an existing destination.
                if os.name == "nt":
                    try:
                        os.rename(source, dest)
                    except OSError as exc:
                        if getattr(exc, "winerror", None) != 17:
                            raise
                        if os.path.isdir(source):
                            reject_junctions(source)
                            shutil.copytree(source, dest, symlinks=True)
                            shutil.rmtree(source)
                        else:
                            with open(source, "rb") as src, open(dest, "xb") as dst:
                                shutil.copyfileobj(src, dst, 1024 * 1024)
                            shutil.copystat(source, dest)
                            os.unlink(source)
                else:
                    shutil.move(source, dest)
            elif os.path.isdir(source):
                reject_junctions(source)
                shutil.copytree(source, dest, symlinks=True)
            elif os.path.islink(source):
                os.symlink(os.readlink(source), dest)
            else:
                with open(source, "rb") as src, open(dest, "xb") as dst:
                    shutil.copyfileobj(src, dst, 1024 * 1024)
                shutil.copystat(source, dest)
        except Exception as exc:
            errors.append(f"{source}: {exc}")
    return errors
