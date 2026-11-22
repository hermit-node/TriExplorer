"""Resolve persistent user files separately from PyInstaller's temporary extraction."""
from __future__ import annotations
import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def application_directory() -> Path:
    """Directory containing the EXE when frozen; the source checkout otherwise."""
    return Path(sys.executable if is_frozen() else __file__).resolve().parent


def bundled_asset(*parts: str) -> Path:
    """Read-only application resources bundled inside PyInstaller's one-file EXE."""
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return root.joinpath(*parts)
