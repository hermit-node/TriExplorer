"""Portable Qt-compatible INI settings without QLockFile on a shared directory.

Qt serializes settings in a private local staging directory. The actual INI
is replaced atomically under a nonblocking OS file lock. No stale-removal
lock chain, and a failed write leaves the existing INI intact.
"""
from __future__ import annotations
from contextlib import contextmanager
import logging
import os
from pathlib import Path
import tempfile
import uuid
from PySide6.QtCore import QSettings

LOG = logging.getLogger(__name__)

@contextmanager
def write_guard(path):
    # Never unlink this guard: the OS releases its byte lock on handle close,
    # including a crashed process. A stable pathname avoids split-lock races.
    with open(path, "a+b") as handle:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class PortableIni:
    def __init__(self, path):
        self.path = Path(path)
        self.directory = tempfile.TemporaryDirectory(prefix="TriExplorer-ini-")
        self.pending = {}
        self.last_error = None
        self._working = self._snapshot()

    def __del__(self):
        # Drop Qt's local file handle before cleaning up the staging directory.
        try:
            self._working = None
            self.directory.cleanup()
        except (AttributeError, OSError):
            pass

    def _snapshot(self):
        staged = Path(self.directory.name) / (uuid.uuid4().hex + ".ini")
        try:
            staged.write_bytes(self.path.read_bytes())
        except FileNotFoundError:
            pass
        return QSettings(str(staged), QSettings.IniFormat)

    def value(self, *args, **kwargs):
        return self._working.value(*args, **kwargs)

    def contains(self, key):
        return self._working.contains(key)

    def allKeys(self):
        return self._working.allKeys()

    def fileName(self):
        return str(self.path)

    def setValue(self, key, value):
        self.pending[key] = value
        self._working.setValue(key, value)

    def sync(self):
        if not self.pending:
            return True
        target = self.path
        temporary = None
        try:
            # Do not race an older executable still using Qt's lock protocol.
            # Do not try to remove its lock or wait recursively for it.
            if target.with_name(target.name + ".lock").exists():
                raise OSError("Legacy settings lock present; close older TriExplorer instances and remove stale locks.")
            guard = target.with_name("." + target.name + ".writeguard")
            with write_guard(guard):
                if target.with_name(target.name + ".lock").exists():
                    raise OSError("Legacy settings lock present.")
                merged = self._snapshot()
                for key, value in self.pending.items():
                    merged.setValue(key, value)
                merged.sync()
                if merged.status() != QSettings.Status.NoError:
                    raise OSError("Unable to serialize settings")
                with tempfile.NamedTemporaryFile(
                    dir=target.parent, prefix="." + target.name + "-", suffix=".tmp", delete=False
                ) as handle:
                    temporary = Path(handle.name)
                    handle.write(Path(merged.fileName()).read_bytes())
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, target)
                temporary = None
                self._working = merged
                self.pending.clear()
            self.last_error = None
            return True
        except OSError as exc:
            self.last_error = str(exc)
            LOG.warning("Settings not saved to %s: %s", target, exc)
            return False
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    LOG.warning("Could not remove settings staging file %s", temporary)
