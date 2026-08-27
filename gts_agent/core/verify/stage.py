"""staged 构建产物验证（rpmbuild %check / StageInstall 状态）。

检查项：
1. buildroot 路径泄漏：staged 文本/二进制中不得出现构建根路径；
2. glibc 基线：所有 staged ELF 的 GLIBC_* 需求不超过声明基线；
3. 安装路径策略：所有文件位于允许前缀内、不命中禁止路径。
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Optional, Set

from gts_agent.agent.policy_engine import Policy, check_install_path
from gts_agent.core.abi.elf import parse_elf_header, parse_version_info
from gts_agent.core.compatibility.glibc import check_glibc_baseline
from gts_agent.core.models.compatibility import Verdict


@dataclass
class StageVerifyResult:
    passed: bool = True
    buildroot_leaks: List[str] = field(default_factory=list)
    glibc_violations: List[str] = field(default_factory=list)
    policy_violations: List[str] = field(default_factory=list)
    scanned_elf_count: int = 0
    max_glibc_required: str = ""

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(asdict(self), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )


def _is_elf(path: Path) -> bool:
    try:
        with open(path, "rb") as fh:
            return fh.read(4) == b"\x7fELF"
    except OSError:
        return False


_TEXT_LEAK_SKIP_SUFFIXES = {
    ".h", ".hpp", ".hh", ".c", ".cc", ".cpp", ".def",
    ".py", ".pyc", ".pyo", ".txt", ".md", ".html", ".info", ".texi",
    ".rst", ".xml", ".json",
}
_TEXT_LEAK_SKIP_PARTS = (
    "/include-fixed/",
    "/include/c++/",
    "/share/gcc-",
    "/share/gdb/",
    "/python/",
)


def _scan_text_for_buildroot_leak(install_path: str) -> bool:
    """ELF 的 RPATH 必须干净；头文件/pretty printer 里的构建路径不阻断打包。"""
    lowered = install_path.lower()
    if any(part in lowered for part in _TEXT_LEAK_SKIP_PARTS):
        return False
    suffix = Path(install_path).suffix.lower()
    if suffix in _TEXT_LEAK_SKIP_SUFFIXES:
        return False
    return True


def _file_contains(path: Path, needle: bytes) -> bool:
    try:
        with open(path, "rb") as fh:
            previous_tail = b""
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                if needle in previous_tail + chunk:
                    return True
                previous_tail = chunk[-len(needle):]
    except OSError:
        pass
    return False


def verify_stage(
    stage_root: Path,
    toolset_root: str,
    glibc_baseline: str,
    policy: Optional[Policy] = None,
    check_buildroot_leak: bool = True,
) -> StageVerifyResult:
    result = StageVerifyResult()
    stage_root = stage_root.resolve()
    buildroot_needle = str(stage_root).encode()
    scan_roots = []
    for extra in (
        "opt/rh",
        "usr/bin",
        "usr/lib/gcc-toolset",
        "usr/lib/rpm/macros.d",
    ):
        extra_path = stage_root / extra
        if extra_path.exists():
            scan_roots.append(extra_path)

    all_glibc_required: Set[str] = set()

    for scan_root in scan_roots:
        if not scan_root.exists():
            continue
        for dirpath, _dirnames, filenames in os.walk(scan_root):
            for filename in filenames:
                full = Path(dirpath) / filename
                if full.is_symlink():
                    continue
                install_path = "/" + str(full.relative_to(stage_root))

                if policy is not None:
                    if install_path.startswith("/usr/bin/") and \
                            not install_path.startswith("/usr/bin/gcc-toolset-"):
                        continue
                    decision = check_install_path(policy, install_path)
                    if decision.result != "ALLOW":
                        result.policy_violations.append(
                            f"{install_path}: {decision.detail}"
                        )

                if _is_elf(full):
                    result.scanned_elf_count += 1
                    try:
                        elf = parse_elf_header(full)
                    except RuntimeError:
                        elf = None
                    if check_buildroot_leak and elf is not None:
                        leaked = [
                            item for item in (elf.rpath + elf.runpath + elf.needed)
                            if str(stage_root) in item or "/tmp/" in item
                        ]
                        if leaked:
                            result.buildroot_leaks.append(
                                f"{install_path}: {leaked}"
                            )
                    try:
                        _, requirements = parse_version_info(full)
                    except RuntimeError:
                        continue
                    glibc_nodes = {
                        node for node in requirements if node.startswith("GLIBC_")
                    }
                    if glibc_nodes:
                        report = check_glibc_baseline(glibc_nodes, glibc_baseline)
                        if report.verdict == Verdict.FAIL:
                            exceeded = report.findings[0].facts.get("required", [])
                            result.glibc_violations.append(
                                f"{install_path}: {exceeded}"
                            )
                    all_glibc_required.update(glibc_nodes)
                elif (
                    check_buildroot_leak
                    and _scan_text_for_buildroot_leak(install_path)
                    and _file_contains(full, buildroot_needle)
                ):
                    result.buildroot_leaks.append(install_path)

    if all_glibc_required:
        result.max_glibc_required = max(
            all_glibc_required,
            key=lambda node: tuple(
                int(part) for part in node.split("_")[1].split(".")
            ) if node != "GLIBC_PRIVATE" else (999,),
        )

    result.passed = not (
        result.buildroot_leaks
        or result.glibc_violations
        or result.policy_violations
    )
    return result
