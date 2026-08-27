"""版本化符号集合计算与 nonshared 差集校验（方案 20.3、11.6）。

核心规则：
- 符号键至少为 (name, version)，只统计已定义、可见性为
  DEFAULT/PROTECTED 的动态符号；
- system-nonshared 模式下：target_delta = target - system 必须被
  nonshared 归档完整覆盖（E-NONSHARED-INCOMPLETE 否则阻断）；
- nonshared 归档不允许有隐藏的未解析符号（NONSHARED_INVALID）；
- 一律禁止伪造符号（SYMBOL_TAMPERING 永久阻断，此模块不提供任何
  添加/改写符号的能力）。
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Set, Tuple

from gts_agent.core.abi.elf import DynamicSymbol, parse_dynamic_symbols

VersionedSymbol = Tuple[str, str]  # (name, version or "")


class CompatibilityError(RuntimeError):
    def __init__(self, code: str, message: str, missing: List[str] = None) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.missing = missing or []


def versioned_symbol_set(symbols: List[DynamicSymbol]) -> Set[VersionedSymbol]:
    """从动态符号列表计算版本化导出符号集合。"""
    result: Set[VersionedSymbol] = set()
    for symbol in symbols:
        if not symbol.defined:
            continue
        if symbol.visibility not in ("DEFAULT", "PROTECTED"):
            continue
        if symbol.binding not in ("GLOBAL", "WEAK", "UNIQUE"):
            continue
        result.add((symbol.name, symbol.version or ""))
    return result


def versioned_symbol_set_of(path: Path) -> Set[VersionedSymbol]:
    return versioned_symbol_set(parse_dynamic_symbols(path))


_NM_LINE_RE = re.compile(
    r"^(?P<archive>\S+?):(?P<member>\S+?):\s*(?:[0-9a-fA-F]+\s+)?(?P<type>\S)\s+(?P<name>\S+)$"
)


def _run_nm(args: List[str], path: Path) -> str:
    result = subprocess.run(
        ["nm", *args, str(path)],
        capture_output=True, text=True, timeout=120, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"nm {' '.join(args)} {path} 失败: {result.stderr.strip()[:300]}")
    return result.stdout


def archive_defined_symbols(path: Path) -> Set[VersionedSymbol]:
    """静态归档（如 libstdc++_nonshared.a）中已定义的全局符号集合。

    静态归档符号本身没有 ELF 版本节点；与共享库差集比较时按名称匹配，
    版本字段规约为 ""。
    """
    output = _run_nm(["-A", "--defined-only"], path)
    result: Set[VersionedSymbol] = set()
    for line in output.splitlines():
        match = _NM_LINE_RE.match(line.strip())
        if not match:
            continue
        symbol_type = match.group("type")
        # 大写为全局符号；u 为 GNU unique global
        if symbol_type.isupper() or symbol_type == "u":
            result.add((match.group("name"), ""))
    return result


def archive_undefined_symbols(path: Path) -> Set[str]:
    output = _run_nm(["-A", "--undefined-only"], path)
    result: Set[str] = set()
    for line in output.splitlines():
        parts = line.strip().split()
        if parts:
            result.add(parts[-1])
    return result


@dataclass
class NonsharedCheckResult:
    target_delta: Set[VersionedSymbol] = field(default_factory=set)
    covered: Set[VersionedSymbol] = field(default_factory=set)
    missing: Set[VersionedSymbol] = field(default_factory=set)
    unexpected_extra: Set[str] = field(default_factory=set)

    @property
    def complete(self) -> bool:
        return not self.missing


def check_nonshared_coverage(
    system_symbols: Set[VersionedSymbol],
    target_symbols: Set[VersionedSymbol],
    nonshared_symbols: Set[VersionedSymbol],
) -> NonsharedCheckResult:
    """校验 nonshared 归档是否完整覆盖 target - system 的 ABI 差集。

    静态归档符号按名称匹配（忽略版本节点），因为 nonshared 中的实现
    以静态方式链接进应用，不携带版本节点。
    """
    target_delta = target_symbols - system_symbols
    nonshared_names = {name for name, _ in nonshared_symbols}
    system_names = {name for name, _ in system_symbols}
    target_names = {name for name, _ in target_symbols}

    covered = {sym for sym in target_delta if sym[0] in nonshared_names}
    missing = target_delta - covered
    # nonshared 提供了既不在差集、也不在系统库中的符号 => 可能静态嵌入了
    # 不应嵌入的 ABI，需要人工审查（方案 11.6）。
    unexpected_extra = nonshared_names - {name for name, _ in target_delta} - system_names
    unexpected_extra &= target_names | (nonshared_names - system_names)

    return NonsharedCheckResult(
        target_delta=target_delta,
        covered=covered,
        missing=missing,
        unexpected_extra=unexpected_extra,
    )


def require_nonshared_complete(result: NonsharedCheckResult) -> None:
    if not result.complete:
        raise CompatibilityError(
            code="E-NONSHARED-INCOMPLETE",
            message=(
                f"nonshared 归档缺少 {len(result.missing)} 个目标 ABI 差集符号"
            ),
            missing=sorted(f"{name}@{version}" for name, version in result.missing),
        )


def validate_nonshared_archive(
    archive_path: Path,
    allowed_undefined_prefixes: Tuple[str, ...] = (
        "_ZSt", "_ZNSt", "_ZdlPv", "_Znwm", "__cxa_", "_Unwind_",
        "__gxx_personality", "memcpy", "memmove", "memset", "malloc", "free",
        "abort", "strlen", "pthread_",
    ),
) -> Dict[str, List[str]]:
    """检查 nonshared 归档的未解析符号是否都是预期的外部依赖。

    出现无法解释的隐藏未定义符号时返回它们，调用方必须阻断
    （E-NONSHARED-MISMATCH / NONSHARED_INVALID）。
    """
    undefined = archive_undefined_symbols(archive_path)
    unexplained = [
        symbol for symbol in sorted(undefined)
        if not symbol.startswith(allowed_undefined_prefixes)
        and not symbol.startswith(("_ZN", "_ZT", "_ZZ", "__", "GLIBC"))
    ]
    return {"undefined": sorted(undefined), "unexplained": unexplained}
