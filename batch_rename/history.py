"""Persistent completed batch journal in the existing explorer database."""
import json
from contextlib import closing
import sqlite3
from pathlib import Path
from .executor import execute, fingerprint

class History:
    def __init__(self, db):
        self.db=Path(db); self.db.parent.mkdir(parents=True,exist_ok=True)
        with closing(sqlite3.connect(self.db)) as c, c:
            c.execute("CREATE TABLE IF NOT EXISTS rename_batches (id INTEGER PRIMARY KEY, directory TEXT, records TEXT, undone INTEGER DEFAULT 0)")
    def apply(self,directory,pairs):
        with closing(sqlite3.connect(self.db)) as c, c:
            c.execute("BEGIN IMMEDIATE")
            records=execute(pairs)
            if records:
                try:
                    c.execute("INSERT INTO rename_batches(directory,records) VALUES (?,?)",(str(directory),json.dumps(records)))
                    c.commit()
                except Exception:
                    execute([(b,a) for a,b,_ in records])
                    raise
            return records
    def undo(self,directory):
        with closing(sqlite3.connect(self.db)) as c, c:
            c.execute("BEGIN IMMEDIATE")
            row=c.execute("SELECT id,records FROM rename_batches WHERE directory=? AND undone=0 ORDER BY id DESC LIMIT 1",(str(directory),)).fetchone()
            if not row: return False
            records=json.loads(row[1])
            for a,b,f in records:
                if fingerprint(b)!=f: raise ValueError("File changed since rename: "+b)
            execute([(b,a) for a,b,f in records])
            try:
                c.execute("UPDATE rename_batches SET undone=1 WHERE id=?",(row[0],))
                c.commit()
            except Exception:
                execute([(a,b) for a,b,f in records])
                raise
            return True
