"""Validate an entire batch before touching any source."""
import os
from pathlib import Path

def key(path):
    return os.path.normcase(os.path.abspath(path))

def validate(pairs):
    errors = {}
    sources = {key(a) for a,b in pairs if str(a) != str(b)}
    targets = {}
    for a,b in pairs:
        a,b = Path(a),Path(b)
        if a == b and str(a) == str(b): continue
        name = b.name
        reserved = {"CON","PRN","AUX","NUL",*(f"COM{i}" for i in range(1,10)),*(f"LPT{i}" for i in range(1,10))}
        if a.parent != b.parent or not name or name in (".","..") or name.endswith((".", " ")) or any(c in name for c in '/\\<>:"|?*') or any(ord(c)<32 for c in name) or name.split(".")[0].upper() in reserved:
            errors[str(a)] = "Invalid destination name"
        elif not os.path.lexists(a): errors[str(a)] = "Source no longer exists"
        elif os.path.lexists(b) and key(b) not in sources: errors[str(a)] = "Destination exists"
        k=key(b)
        if k in targets:
            errors[str(a)] = errors[targets[k]] = "Duplicate destination"
        targets[k]=str(a)
    return errors
