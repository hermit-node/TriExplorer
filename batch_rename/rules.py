"""Pure filename transformations, applied in user-defined order."""
import re
from pathlib import Path

OPTIONS = {
    "Replace text": ("Find", "Replace with"),
    "Remove text": ("Text", ""),
    "Add prefix": ("Prefix", ""),
    "Add suffix": ("Suffix", ""),
    "Insert text": ("Text", "Position (zero-based)"),
    "Remove first characters": ("Count", ""),
    "Remove last characters": ("Count", ""),
    "Remove between markers": ("Start marker", "End marker"),
    "Keep between markers": ("Start marker", "End marker"),
    "Remove numbers": ("", ""),
    "Remove symbols": ("", ""),
    "Normalize spaces": ("", ""),
    "Change capitalization": ("lower / upper / title / sentence / snake / kebab / camel", ""),
    "Sequential numbering": ("Starting number", "Padding"),
    "Change extension": ("New extension", ""),
    "Custom regex": ("Pattern", "Replacement"),
}

def transform(name, rules, index=0):
    stem, ext = Path(name).stem, Path(name).suffix
    for kind, a, b in rules:
        if kind == "Replace text":
            if a: stem = stem.replace(a, b)
        elif kind == "Remove text":
            if a: stem = stem.replace(a, "")
        elif kind == "Add prefix": stem = a + stem
        elif kind == "Add suffix": stem += a
        elif kind == "Insert text":
            pos = max(0, int(b)); stem = stem[:pos] + a + stem[pos:]
        elif kind == "Remove first characters": stem = stem[max(0,int(a)):]
        elif kind == "Remove last characters":
            count = max(0,int(a)); stem = stem[:-count] if count else stem
        elif kind in ("Remove between markers", "Keep between markers"):
            if not a or not b: raise ValueError("Both markers are required")
            left, found, tail = stem.partition(a)
            middle, end, right = tail.partition(b)
            if found and end: stem = left+a+b+right if kind.startswith("Remove") else middle
        elif kind == "Remove numbers": stem = re.sub(r"\d+", "", stem)
        elif kind == "Remove symbols": stem = re.sub(r"[^\w\s]|_", "", stem)
        elif kind == "Normalize spaces": stem = " ".join(stem.split())
        elif kind == "Change capitalization":
            words = re.findall(r"[^\W_]+", stem)
            modes = {"lower": stem.lower(), "upper": stem.upper(), "title": stem.title(),
                     "sentence": stem.capitalize(), "snake": "_".join(words).lower(),
                     "kebab": "-".join(words).lower(),
                     "camel": (words[0].lower()+"".join(w.capitalize() for w in words[1:])) if words else ""}
            stem = modes[a]
        elif kind == "Sequential numbering":
            padding = int(b or 1)
            if not 1 <= padding <= 32: raise ValueError("Padding must be 1–32")
            stem += "_" + str(int(a or 1)+index).zfill(padding)
        elif kind == "Change extension": ext = "."+a.lstrip(".") if a else ""
        elif kind == "Custom regex": stem = re.sub(a,b,stem)
    return stem + ext
