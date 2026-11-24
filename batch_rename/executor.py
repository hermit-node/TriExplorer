"""Two-phase rename with rollback and no silent overwrites."""
import os
from pathlib import Path
from uuid import uuid4
from .validator import validate

def fingerprint(path):
    s=os.lstat(path)
    return [s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns]

def move(a,b):
    if os.path.lexists(b): raise FileExistsError(str(b))
    # Windows rename is non-overwriting; POSIX hard-link avoids overwrite races.
    if os.name == "nt": os.rename(a,b)
    else:
        os.link(a,b,follow_symlinks=False)
        try: os.unlink(a)
        except OSError:
            os.unlink(b)
            raise

def execute(pairs):
    pairs=[(Path(a),Path(b)) for a,b in pairs if str(a)!=str(b)]
    errors=validate(pairs)
    if errors: raise ValueError(str(errors))
    staged=[]; completed=[]
    try:
        for a,b in pairs:
            if not a.is_file() or a.is_symlink(): raise ValueError("Only regular files are supported")
            tmp=a.with_name(".triexplorer-rename-"+uuid4().hex)
            move(a,tmp); staged.append((a,tmp,b))
        for a,tmp,b in staged:
            move(tmp,b); completed.append((a,tmp,b))
        return [(str(a),str(b),fingerprint(b)) for a,tmp,b in completed]
    except Exception as exc:
        failures=[]
        for a,tmp,b in reversed(completed):
            try: move(b,tmp)
            except OSError as e: failures.append(str(e))
        for a,tmp,b in reversed(staged):
            if os.path.lexists(tmp):
                try: move(tmp,a)
                except OSError as e: failures.append(str(e))
        if failures: raise RuntimeError(f"{exc}; rollback incomplete: {failures}") from exc
        raise
