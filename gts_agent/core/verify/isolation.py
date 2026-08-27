import os
from pathlib import Path
from typing import Dict, List


SNAPSHOT_PATHS = [
    "/usr/bin/gcc",
    "/usr/bin/g++",
    "/usr/bin/cc",
    "/usr/bin/ld",
    "/usr/bin/as",
    "/usr/lib64/libstdc++.so.6",
    "/lib64/libgcc_s.so.1",
    "/etc/ld.so.conf",
]

SNAPSHOT_DIRS = [
    "/etc/ld.so.conf.d",
]


def _sha256(path: Path) -> str:
    if not path.exists() or path.is_dir():
        return ""
    if path.is_symlink():
        return f"symlink:{os.readlink(path)}"
    import hashlib
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def take_snapshot(extra_paths: List[str] = None) -> Dict[str, str]:
    snapshot = {}
    for raw in SNAPSHOT_PATHS + list(extra_paths or []):
        snapshot[raw] = _sha256(Path(raw))
    for directory in SNAPSHOT_DIRS:
        root = Path(directory)
        if not root.is_dir():
            snapshot[directory] = ""
            continue
        for child in sorted(root.iterdir()):
            if child.is_file() or child.is_symlink():
                snapshot[str(child)] = _sha256(child)
    return snapshot


def compare_snapshots(before: Dict[str, str], after: Dict[str, str]) -> List[str]:
    return [path for path, digest in before.items() if after.get(path) != digest]


def save_snapshot(snapshot: Dict[str, str], path: Path) -> None:
    import json
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8")
