"""Filesystem row definitions and formatting helpers, independent of Qt."""
from __future__ import annotations
import os
from dataclasses import dataclass

PC = ""

def normalized(path):
    return os.path.normcase(os.path.normpath(path)) if path else PC


def readable_size(n):
    if n is None:
        return ""
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if value < 1024 or unit == "PB":
            return f"{int(value)} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024


@dataclass(frozen=True)
class Entry:
    path: str
    name: str
    folder: bool
    archive: bool = False
    size: int | None = None
    modified: float | None = None
    hidden: bool = False
