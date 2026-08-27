"""glibc 基线判定：目标产物的 GLIBC_* 需求不得超过声明基线。

算法（方案 9.4）：
    required = union(version_requirements(toolset_elf))
    provided = version_definitions(baseline_glibc_objects)
    required - provided 非空 => E-GLIBC-BASELINE，禁止自动修复。
"""

from __future__ import annotations

import re
from typing import Iterable, Optional, Set, Tuple

from gts_agent.core.models.compatibility import CompatibilityReport, Finding, Verdict

_VERSION_RE = re.compile(r"^GLIBC_(\d+)\.(\d+)(?:\.(\d+))?$")


def parse_glibc_version_node(node: str) -> Tuple[int, ...]:
    match = _VERSION_RE.match(node)
    if not match:
        raise ValueError(f"不是合法的 GLIBC 版本节点: {node!r}")
    return tuple(int(part) for part in match.groups() if part is not None)


def expand_baseline(baseline: str) -> str:
    """把 '2.34' 规范成 'GLIBC_2.34'。"""
    return baseline if baseline.startswith("GLIBC_") else f"GLIBC_{baseline}"


def check_glibc_baseline(
    required_nodes: Iterable[str],
    baseline_version: str,
    provided_nodes: Optional[Iterable[str]] = None,
) -> CompatibilityReport:
    """required_nodes 为所有目标 ELF 的 GLIBC_* 版本需求并集。

    baseline_version 为运行基线 glibc 版本（如 "2.34"）。
    若提供 provided_nodes（构建根 libc.so.6 的版本定义），则按
    required - provided 判定——RHEL 9 的 glibc-2.34 会回移植
    GLIBC_2.35 等节点，不能只用版本号比较。
    未提供 provided_nodes 时，退回为「需求节点 <= 基线版本号」。
    """
    report = CompatibilityReport()
    baseline_node = expand_baseline(baseline_version)
    baseline_tuple = parse_glibc_version_node(baseline_node)
    provided: Optional[Set[str]] = None
    if provided_nodes is not None:
        provided = {
            node for node in provided_nodes
            if node.startswith("GLIBC_") and node != "GLIBC_PRIVATE"
        }

    exceeded: Set[str] = set()
    for node in required_nodes:
        if not node.startswith("GLIBC_"):
            continue
        if node == "GLIBC_PRIVATE":
            exceeded.add(node)
            continue
        if provided is not None:
            if node not in provided:
                exceeded.add(node)
            continue
        try:
            if parse_glibc_version_node(node) > baseline_tuple:
                exceeded.add(node)
        except ValueError:
            exceeded.add(node)

    if exceeded:
        report.add(Finding(
            verdict=Verdict.FAIL,
            reason_code="E-GLIBC-BASELINE",
            message=(
                f"目标产物要求的 glibc 版本节点 {sorted(exceeded)} 超过声明基线 "
                f"{baseline_node}"
            ),
            facts={
                "baseline_max": baseline_node,
                "required": sorted(exceeded),
            },
            allowed_actions=[
                "use_older_buildroot",
                "choose_older_target_gcc",
                "port_source_patch",
            ],
            forbidden_actions=[
                "copy_newer_glibc_into_toolset",
                "modify_system_loader",
            ],
        ))
    return report
