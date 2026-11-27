"""Read ISO filesystem members without mounting or extraction."""
from dataclasses import dataclass
from contextlib import contextmanager
from collections import Counter
from services.filesystem import valid_name
import os
import re

@dataclass(frozen=True)
class IsoMember:
    file_size: int
    namespace: str
    path: str
    flag_bits: int = 0

def members_for(image, namespace, cancelled):
    records, seen, extents = [], set(), Counter()
    for base, dirs, files in image.walk(**{namespace: "/"}):
        for names, directory in ((dirs, True), (files, False)):
            for name in names:
                if cancelled():
                    raise InterruptedError()
                path = base.rstrip("/") + "/" + name
                parts = tuple(path.strip("/").split("/"))
                if namespace == "iso_path" and not directory:
                    parts = (*parts[:-1], re.sub(r";\d+$", "", parts[-1]).rstrip("."))
                if not all(valid_name(part) and not getattr(os.path, "isreserved", lambda _: False)(part) for part in parts):
                    raise ValueError("Unsafe ISO member: " + path)
                key = tuple(part.casefold() for part in parts)
                if key in seen:
                    raise ValueError("Case-colliding ISO member: " + path)
                seen.add(key)
                record = image.get_record(**{namespace: path})
                if getattr(record, "is_symlink", lambda: False)():
                    raise ValueError("ISO contains symbolic link: " + path)
                if not directory and not record.is_file():
                    raise ValueError("ISO contains non-regular file: " + path)
                size = 0 if directory else record.get_data_length()
                if not directory:
                    extents[(record.extent_location(), size)] += 1
                records.append((IsoMember(size, namespace, path), parts, directory))
    return records, extents

class IsoReader:
    def __init__(self, image):
        self.image = image
    def open(self, member, pwd=None):
        return self.image.open_file_from_iso(**{member.namespace: member.path})

@contextmanager
def open_iso(path, cancelled):
    import pycdlib
    image = pycdlib.PyCdlib()
    image.open(path)
    try:
        namespaces = [name for name, available in (
            ("udf_path", image.has_udf()), ("joliet_path", image.has_joliet()),
            ("rr_path", image.has_rock_ridge()), ("iso_path", True)) if available]
        members, extents = members_for(image, namespaces[0], cancelled)
        with open(path, "rb") as f:
            system_area = f.read(16 * 2048)
        protected = bool(image.eltorito_boot_catalog or image.isohybrid_mbr
                         or image.udf_boots or any(system_area))
        for namespace in namespaces[1:]:
            try:
                _, other = members_for(image, namespace, cancelled)
                if other != extents:
                    protected = True
            except InterruptedError:
                raise
            except Exception:
                protected = True
        yield IsoReader(image), members, False, protected
    finally:
        image.close()
