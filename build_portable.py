"""Build and package a self-contained Windows TriExplorer release.

Run: py -3.14 -B build_portable.py --version 0.1.0
Requirements: PyInstaller and project requirements installed in this Python.
The release archive has ONLY TriExplorer.exe, settings.ini and TriExplorer.sqlite3.
"""
from __future__ import annotations
import argparse
import hashlib
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parent


def build(version: str) -> Path:
    if sys.platform != "win32":
        raise RuntimeError("Windows build required for the portable .exe")
    if not version or any(ch not in "0123456789." for ch in version):
        raise ValueError("Version must be a dotted numeric version, e.g. 0.1.0")

    label = f"TriExplorer-v{version}-win64"
    dist = ROOT / "dist"
    staging = dist / label
    staging.mkdir(parents=True, exist_ok=True)
    exe = staging / "TriExplorer.exe"
    output = dist / f"{label}.zip"

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onefile", "--windowed",
        "--name", "TriExplorer",
        "--icon", str(ROOT / "assets" / "triexplorer.ico"),
        "--add-data", str(ROOT / "assets" / "triexplorer.ico") + os.pathsep + "assets",
        "--distpath", str(staging),
        "--workpath", str(dist / "_pyinstaller" / "build"),
        "--specpath", str(dist / "_pyinstaller" / "spec"),
        str(ROOT / "main.py"),
    ]
    subprocess.run(cmd, cwd=ROOT, check=True)
    if not exe.is_file() or exe.stat().st_size < 1_000_000:
        raise RuntimeError(f"PyInstaller executable missing or invalid: {exe}")

    shutil.copyfile(ROOT / "settings.example.ini", staging / "settings.ini")
    db = staging / "TriExplorer.sqlite3"
    # A new, blank local cache; never ship the developer's cache or settings.
    if db.exists():
        db.unlink()
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE folder_sizes "
            "(path TEXT PRIMARY KEY, bytes INTEGER NOT NULL, "
            "checked REAL NOT NULL, modified REAL NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE sha_cache "
            "(path TEXT PRIMARY KEY, signature TEXT, digest BLOB, checked REAL)"
        )
        conn.commit()

    output.unlink(missing_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name in ("TriExplorer.exe", "settings.ini", "TriExplorer.sqlite3"):
            archive.write(staging / name, arcname=name)

    for file in (exe, output):
        sha = hashlib.sha256(file.read_bytes()).hexdigest()
        print(f"SHA256 {sha} {file.name}", flush=True)
    print(f"RELEASE_ZIP={output}", flush=True)
    print(f"RELEASE_DIR={staging}", flush=True)
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", default="0.1.0")
    args = parser.parse_args()
    build(args.version)
