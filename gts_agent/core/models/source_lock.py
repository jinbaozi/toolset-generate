"""SourceLock：锁定所有源码输入（SRPM、tarball、patch、仓库快照）。

任何哈希不一致都必须立即失败（E-SOURCE-HASH），不允许自动"修复"。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


class SourceHashMismatch(RuntimeError):
    code = "E-SOURCE-HASH"


@dataclass
class LockedSource:
    name: str
    type: str  # srpm | tarball | git-snapshot | patch
    uri: str
    sha256: str
    nevr: Optional[str] = None
    commit: Optional[str] = None
    local_path: Optional[str] = None


@dataclass
class LockedPatch:
    id: str
    origin: str
    source_file: str
    sha256: str
    strip: int = 1
    fuzz_allowed: int = 0
    risk: str = "high"
    apply_order: int = 0
    applies_to: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SourceLock:
    schema_version: int = 1
    sources: List[LockedSource] = field(default_factory=list)
    patches: List[LockedPatch] = field(default_factory=list)
    repo_snapshot_id: str = ""
    source_date_epoch: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def canonical_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False, separators=(",", ":"))

    def fingerprint_component(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: Path) -> "SourceLock":
        data = json.loads(path.read_text(encoding="utf-8"))
        sources = [LockedSource(**s) for s in data.get("sources", [])]
        patches = [LockedPatch(**p) for p in data.get("patches", [])]
        return cls(
            schema_version=data.get("schema_version", 1),
            sources=sources,
            patches=patches,
            repo_snapshot_id=data.get("repo_snapshot_id", ""),
            source_date_epoch=data.get("source_date_epoch"),
        )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_source(path: Path, expected_sha256: str) -> str:
    actual = sha256_file(path)
    if expected_sha256 and expected_sha256 != "<required>" and actual != expected_sha256:
        raise SourceHashMismatch(
            f"[E-SOURCE-HASH] {path} 哈希不符：期望 {expected_sha256}，实际 {actual}"
        )
    return actual
