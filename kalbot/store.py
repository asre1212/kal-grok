"""Compact JSON persistence (gzip for bulky rolling data)."""
import gzip
import json
from pathlib import Path


def load_json(path, default):
    path = Path(path)
    if not path.exists():
        return default
    if path.suffix == ".gz":
        with gzip.open(path, "rt") as fh:
            return json.load(fh)
    return json.loads(path.read_text())


def save_json(path, obj, compact=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".gz":
        data = json.dumps(obj, separators=(",", ":")).encode()
        # mtime=0 -> byte-identical output for identical data (smaller git diffs)
        with open(path, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as fh:
            fh.write(data)
        return
    txt = json.dumps(obj, separators=(",", ":")) if compact else json.dumps(obj, indent=1)
    path.write_text(txt)
