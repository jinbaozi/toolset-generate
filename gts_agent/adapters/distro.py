"""发行版适配器：RHEL/CentOS Stream 9 与 10 的差异隔离（方案 15.4）。

不维护"一份万能 Spec"；所有版本差异（SCL vs env 激活、RPM 宏能力、
nonshared 基线）都在适配器内声明。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass(frozen=True)
class DistroAdapter:
    name: str
    distro_id: str
    major: int
    rpm_adapter: str            # rpm-4.16 / rpm-4.19
    toolset_framework: str      # scl-utils / gts-env
    activation: str             # scl-enable / env-wrapper
    spec_renderer: str          # rhel9-scl / rhel10-gts-env
    nonshared_baseline: str     # 参考 nonsharedver（仅对参考 profile 有意义）
    extra_configure_flags: List[str] = field(default_factory=list)


RHEL9 = DistroAdapter(
    name="rhel9",
    distro_id="centos-stream",
    major=9,
    rpm_adapter="rpm-4.16",
    toolset_framework="scl-utils",
    activation="scl-enable",
    spec_renderer="rhel9-scl",
    nonshared_baseline="110",
    extra_configure_flags=[
        "--enable-default-pie",
        "--enable-default-ssp",
        "--enable-gnu-unique-object",
    ],
)

RHEL10 = DistroAdapter(
    name="rhel10",
    distro_id="centos-stream",
    major=10,
    rpm_adapter="rpm-4.19",
    toolset_framework="gts-env",
    activation="env-wrapper",
    spec_renderer="rhel10-gts-env",
    nonshared_baseline="140",
    extra_configure_flags=[
        "--enable-default-pie",
        "--enable-default-ssp",
        "--enable-gnu-unique-object",
        "--enable-libstdcxx-backtrace",
    ],
)

_ADAPTERS: Dict[tuple, DistroAdapter] = {
    ("centos-stream", 9): RHEL9,
    ("rhel", 9): RHEL9,
    ("centos-stream", 10): RHEL10,
    ("rhel", 10): RHEL10,
}


class AdapterError(RuntimeError):
    pass


def get_adapter(distro_id: str, major: int) -> DistroAdapter:
    key = (distro_id, major)
    if key not in _ADAPTERS:
        raise AdapterError(
            f"没有可用的发行版适配器: {distro_id} {major}（MVP 仅支持 RHEL/CentOS Stream 9、10）"
        )
    adapter = _ADAPTERS[key]

    expected_rpm = {9: "rpm-4.16", 10: "rpm-4.19"}[major]
    if adapter.rpm_adapter != expected_rpm:
        raise AdapterError(
            f"适配器 {adapter.name} 的 RPM 版本 {adapter.rpm_adapter} 与发行版预期 {expected_rpm} 不符"
        )
    return adapter
