"""GCC 兼容性分析器：bootstrap 语言要求、triple 一致性、版本关系。

判定规则来自方案 9.1 与 GCC 上游 prerequisites：
- GCC 需要 ISO C++14 编译器引导（GCC 5.4+ 通常满足）；
- GCC 15 以前可由 C++11 编译器引导；
- triple 不一致且未声明 cross-toolchain 时快速失败。
"""

from __future__ import annotations

from typing import Optional

from gts_agent.core.models.compatibility import (
    CompatibilityReport,
    Finding,
    Verdict,
)
from gts_agent.core.models.config import JobConfig
from gts_agent.core.models.inventory import Inventory

# GCC 上游要求：引导目标 GCC 所需的最低基础 GCC 主版本。
# 键为目标 GCC 主版本下限；GCC >= 15 需要 C++14（GCC 5.4+ 保守取 6），
# GCC 5..14 可由 C++11 编译器引导（保守取 4.8 -> 主版本 5 作为可自动资格线）。
MIN_BASE_MAJOR_FOR_TARGET = [
    (15, 6),
    (5, 5),
]


def minimum_base_major(target_major: int) -> int:
    for threshold, minimum in MIN_BASE_MAJOR_FOR_TARGET:
        if target_major >= threshold:
            return minimum
    return 4


def check_triple(config: JobConfig, inventory: Inventory, report: CompatibilityReport) -> None:
    actual = inventory.gcc.dumpmachine if inventory.gcc else ""
    expected = config.platform.target_triple
    if not actual:
        report.add(Finding(
            verdict=Verdict.WARN,
            reason_code="W-TRIPLE-UNKNOWN",
            message="无法从基础 GCC 获取 -dumpmachine 输出",
        ))
        return
    if actual != expected:
        report.add(Finding(
            verdict=Verdict.FAIL,
            reason_code="E-TRIPLE-MISMATCH",
            message=(
                f"基础 GCC dumpmachine={actual!r} 与目标 triple {expected!r} 不一致，"
                "且当前配置未声明 cross-toolchain（MVP 仅支持 native）"
            ),
            facts={"dumpmachine": actual, "target_triple": expected},
            forbidden_actions=["ignore_triple_mismatch"],
        ))


def check_bootstrap(
    config: JobConfig, inventory: Inventory, report: CompatibilityReport
) -> None:
    base_major: Optional[int] = inventory.gcc.major if inventory.gcc else None
    target_major = config.target_gcc.major

    if base_major is None:
        report.add(Finding(
            verdict=Verdict.FAIL,
            reason_code="E-BOOTSTRAP",
            message="无法解析基础 GCC 版本，不能验证 bootstrap 要求",
        ))
        return

    if config.base_gcc.expected_major is not None and base_major != config.base_gcc.expected_major:
        report.add(Finding(
            verdict=Verdict.FAIL,
            reason_code="E-BASE-GCC-UNEXPECTED",
            message=(
                f"基础 GCC 主版本 {base_major} 与配置声明 expected_major="
                f"{config.base_gcc.expected_major} 不一致"
            ),
            facts={"actual_major": base_major,
                   "expected_major": config.base_gcc.expected_major},
        ))

    minimum = minimum_base_major(target_major)
    if base_major < minimum:
        report.add(Finding(
            verdict=Verdict.FAIL,
            reason_code="E-BOOTSTRAP",
            message=(
                f"基础 GCC {base_major} 不满足目标 GCC {target_major} 的引导要求"
                f"（至少需要 GCC {minimum}）；未批准中间编译器"
            ),
            facts={"base_major": base_major, "target_major": target_major,
                   "minimum_base_major": minimum},
            allowed_actions=["approve_intermediate_compiler"],
            forbidden_actions=["disable_bootstrap_silently"],
        ))

    if target_major < base_major:
        report.add(Finding(
            verdict=Verdict.WARN,
            reason_code="W-TARGET-OLDER-THAN-BASE",
            message=(
                f"目标 GCC {target_major} 低于基础 GCC {base_major}；"
                "允许构建，但 nonshared/ABI 差集方向需要人工确认"
            ),
        ))


def check_runtime_strategy(config: JobConfig, report: CompatibilityReport) -> None:
    """system-nonshared 仅允许已验证的黄金 profile 版本组合。"""
    if config.toolset.runtime_strategy != "system-nonshared":
        return
    known = {
        (9, "14.2.1"),
        (10, "15.2.1"),
    }
    key = (config.platform.distro.major, config.target_gcc.version)
    if key not in known:
        report.add(Finding(
            verdict=Verdict.FAIL,
            reason_code="E-NONSHARED-MISMATCH",
            message=(
                f"system-nonshared 没有与 {config.platform.distro.id} "
                f"{config.platform.distro.major} / GCC {config.target_gcc.version} "
                "对应的已验证 compat patch；请改用 private-runtime 或接入已验证 profile"
            ),
            allowed_actions=["use_private_runtime", "port_compat_patch"],
            forbidden_actions=["copy_base_gcc_nonshared", "forge_symbols"],
        ))


def analyze_gcc(config: JobConfig, inventory: Inventory) -> CompatibilityReport:
    report = CompatibilityReport()
    check_triple(config, inventory, report)
    check_bootstrap(config, inventory, report)
    check_runtime_strategy(config, report)
    return report
