from pathlib import Path
from .rules import transform
from .validator import validate

def preview(paths,rules):
    pairs=[]; errors={}
    for i,p in enumerate(map(Path,paths)):
        try:
            name=transform(p.name,rules,i)
            if not name or "/" in name or chr(92) in name: raise ValueError("Invalid filename")
            pairs.append((p,p.with_name(name)))
        except Exception as e: errors[str(p)]=str(e)
    errors.update(validate(pairs))
    return pairs,errors
