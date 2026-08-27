"""安装后 ABI / 符号 / loader 检查（方案 17.5）。"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from gts_agent.core.abi.elf import parse_elf_header, parse_version_info
from gts_agent.core.compatibility.glibc import check_glibc_baseline
from gts_agent.core.models.compatibility import Verdict


_CXX_DYNAMIC_CASES = {
    "hello-cpp", "exceptions", "rtti", "threads", "filesystem",
    "dual-abi-1", "dual-abi-0",
}


def private_runtime_link_issues(binary_name: str, needed: List[str]) -> List[str]:
    """private-runtime 下检查验证矩阵二进制的 libstdc++ 链接方式。"""
    issues: List[str] = []
    if binary_name in _CXX_DYNAMIC_CASES and "libstdc++.so.6" not in needed:
        issues.append(f"{binary_name} 未链接 libstdc++.so.6")
    if binary_name == "static-libstdcxx" and "libstdc++.so.6" in needed:
        issues.append("static-libstdc++ 仍依赖 libstdc++.so.6")
    return issues


def analyze_binary(
    path: Path,
    glibc_baseline: str,
    runtime_strategy: str,
    toolset_libdir: str,
    provided_glibc_nodes: Optional[List[str]] = None,
) -> Dict[str, object]:
    elf = parse_elf_header(path)
    _defs, reqs = parse_version_info(path)
    glibc_nodes = [node for node in reqs if node.startswith("GLIBC_")]
    glibc_report = check_glibc_baseline(
        glibc_nodes, glibc_baseline, provided_nodes=provided_glibc_nodes
    )
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
        issues.extend(private_runtime_link_issues(path.name, elf.needed))
    return {
        "path": str(path),
        "needed": elf.needed,
        "rpath": elf.rpath,
        "runpath": elf.runpath,
        "version_requirements": sorted(reqs),
        "issues": issues,
        "passed": not issues,
    }
