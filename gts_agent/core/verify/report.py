"""SBOM / provenance / 构建报告（方案 16、17.8）。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from gts_agent import __version__


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_sbom(rpms: List[Path]) -> Dict[str, Any]:
    components = []
    for rpm in rpms:
        components.append({
            "type": "rpm",
            "name": rpm.name,
            "sha256": sha256_file(rpm),
        })
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "components": components,
    }


def build_provenance(
    job_name: str,
    fingerprint: str,
    source_lock: Dict[str, Any],
    image: str,
    rpms: List[Path],
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload = {
        "builder": f"gts-agent {__version__}",
        "job": job_name,
        "job_fingerprint": fingerprint,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "build_image": image,
        "source_lock": source_lock,
        "artifacts": [
            {"path": str(path), "sha256": sha256_file(path)} for path in rpms
        ],
    }
    if extra:
        payload.update(extra)
    return payload


def write_json(data: Dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
