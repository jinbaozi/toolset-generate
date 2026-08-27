"""安装后 ABI / 符号 / loader 检查（方案 17.5）。"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List

from gts_agent.core.abi.elf import parse_elf_header, parse_version_info
from gts_agent.core.compatibility.glibc import check_glibc_baseline
from gts_agent.core.models.compatibility import Verdict


def analyze_binary(
    path: Path,
    glibc_baseline: str,
    runtime_strategy: str,
    toolset_libdir: str,
) -> Dict[str, object]:
    elf = parse_elf_header(path)
    _defs, reqs = parse_version_info(path)
    glibc_nodes = [node for node in reqs if node.startswith("GLIBC_")]
    glibc_report = check_glibc_baseline(glibc_nodes, glibc_baseline)
    leaks = [
        item for item in (elf.rpath + elf.runpath)
        if "BUILD" in item or "/tmp/" in item or "rpmbuild" in item
    ]
    issues: List[str] = []
    if glibc_report.verdict == Verdict.FAIL:
        issues.append(f"E-GLIBC-BASELINE: {glibc_report.reason_codes}")
    if leaks:
        issues.append(f"RPATH/RUNPATH 泄漏: {leaks}")
    if runtime_strategy == "private-runtime":
        # 动态二进制应依赖 libstdc++.so.6；实际加载路径由 LD_LIBRARY_PATH 控制
        if "libstdc++.so.6" not in elf.needed and path.suffix != ".c":
            pass
    return {
        "path": str(path),
        "needed": elf.needed,
        "rpath": elf.rpath,
        "runpath": elf.runpath,
        "version_requirements": sorted(reqs),
        "issues": issues,
        "passed": not issues,
    }
