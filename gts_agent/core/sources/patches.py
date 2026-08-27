"""patch 零 fuzz 应用器（方案 20.2）。

规则：
- 一律 --fuzz=0；出现 fuzz 或 reject 即 E-PATCH-CONTEXT / E-PATCH-SEMANTIC；
- 先 --dry-run 检查再实际应用；
- 每个补丁应用前后记录哈希，供审计。
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Optional

import yaml


class PatchError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code


@dataclass
class PatchApplication:
    patch_id: str
    patch_file: str
    patch_sha256: str
    strip: int
    dry_run_ok: bool = False
    applied: bool = False
    detail: str = ""


@dataclass
class PatchReport:
    source_root: str
    applications: List[PatchApplication] = field(default_factory=list)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {"source_root": self.source_root,
                 "applications": [asdict(a) for a in self.applications]},
                indent=2, ensure_ascii=False,
            ) + "\n",
            encoding="utf-8",
        )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run_patch(
    patch_file: Path, source_root: Path, strip: int, dry_run: bool
) -> subprocess.CompletedProcess:
    argv = ["patch", "--fuzz=0", f"-p{strip}", "--no-backup-if-mismatch"]
    if dry_run:
        argv.append("--dry-run")
    with open(patch_file, "rb") as fh:
        return subprocess.run(
            argv, stdin=fh, cwd=source_root,
            capture_output=True, text=True, timeout=600, check=False,
        )


def apply_patch(
    patch_file: Path,
    source_root: Path,
    strip: int = 1,
    patch_id: str = "",
    expected_sha256: str = "",
) -> PatchApplication:
    if shutil.which("patch") is None:
        raise PatchError("E-PATCH-CONTEXT", "宿主缺少 patch 命令")
    if not patch_file.exists():
        raise PatchError("E-PATCH-CONTEXT", f"补丁不存在: {patch_file}")

    actual_sha = _sha256(patch_file)
    if expected_sha256 and actual_sha != expected_sha256:
        raise PatchError(
            "E-SOURCE-HASH",
            f"补丁 {patch_file.name} 哈希不符：期望 {expected_sha256}，实际 {actual_sha}",
        )

    application = PatchApplication(
        patch_id=patch_id or patch_file.stem,
        patch_file=str(patch_file),
        patch_sha256=actual_sha,
        strip=strip,
    )

    dry = _run_patch(patch_file, source_root, strip, dry_run=True)
    output = dry.stdout + dry.stderr
    if dry.returncode != 0:
        code = "E-PATCH-SEMANTIC" if "FAILED" in output or "reject" in output \
            else "E-PATCH-CONTEXT"
        raise PatchError(code, f"补丁 {patch_file.name} dry-run 失败:\n{output[:800]}")
    if "fuzz" in output.lower() or "offset" in output.lower():
        # 偏移/fuzz：按方案 2.4 只能重新生成规范化 patch，不允许带 fuzz 应用
        raise PatchError(
            "E-PATCH-CONTEXT",
            f"补丁 {patch_file.name} 需要 fuzz/offset 才能应用（禁止）:\n{output[:800]}",
        )
    application.dry_run_ok = True

    real = _run_patch(patch_file, source_root, strip, dry_run=False)
    if real.returncode != 0:
        raise PatchError(
            "E-PATCH-SEMANTIC",
            f"补丁 {patch_file.name} 实际应用失败:\n{(real.stdout + real.stderr)[:800]}",
        )
    application.applied = True
    application.detail = real.stdout.strip()[:400]
    return application


def apply_patch_manifest(
    manifest_path: Path,
    source_root: Path,
    patches_dir: Optional[Path] = None,
) -> PatchReport:
    """按 manifest（YAML，方案 13.1 格式）顺序应用全部补丁。"""
    report = PatchReport(source_root=str(source_root))
    if not manifest_path.exists():
        return report
    data = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    patches = data.get("patches", []) or []
    base_dir = patches_dir or manifest_path.parent
    for entry in patches:
        if int(entry.get("fuzz_allowed", 0)) != 0:
            raise PatchError(
                "E-POLICY",
                f"补丁 {entry.get('id')} 声明 fuzz_allowed != 0，策略禁止",
            )
        application = apply_patch(
            patch_file=base_dir / entry["source_file"],
            source_root=source_root,
            strip=int(entry.get("strip", 1)),
            patch_id=str(entry.get("id", "")),
            expected_sha256=str(entry.get("sha256", "")),
        )
        report.applications.append(application)
    return report
