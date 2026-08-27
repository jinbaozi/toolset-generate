"""JobConfig：任务配置的解析、校验与规范化。

配置文件为 YAML（见 examples/cs9-gts14.yaml），schema 见
gts_agent/schemas/job-config.schema.json。此模块提供确定性的
解析与快速失败校验；不做任何猜测式修复。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

SUPPORTED_SCHEMA_VERSION = 1

RUNTIME_STRATEGIES = ("system-nonshared", "private-runtime")
SUPPORTED_DISTROS = {
    ("centos-stream", 9),
    ("centos-stream", 10),
    ("rhel", 9),
    ("rhel", 10),
}
SUPPORTED_ARCHITECTURES = ("x86_64", "aarch64")
PACKAGING_LAYOUTS = ("recommended-closure", "strict-two-package")


class ConfigError(ValueError):
    """配置解析或校验失败。带有稳定的错误码前缀，便于上层分类。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code


def _require(data: Dict[str, Any], key: str, context: str) -> Any:
    if key not in data or data[key] is None:
        raise ConfigError("E-CONFIG-MISSING", f"{context} 缺少必填字段 '{key}'")
    return data[key]


@dataclass(frozen=True)
class DistroConfig:
    id: str
    major: int


@dataclass(frozen=True)
class PlatformConfig:
    distro: DistroConfig
    architecture: str
    target_triple: str
    rpm_adapter: str
    multilib_enabled: bool = False


@dataclass(frozen=True)
class BaseGccConfig:
    source: str
    executable: str
    expected_major: Optional[int] = None


@dataclass(frozen=True)
class TargetGccConfig:
    version: str
    languages: List[str]
    bootstrap: str
    source_ref: str

    @property
    def major(self) -> int:
        return int(self.version.split(".")[0])


@dataclass(frozen=True)
class BinutilsConfig:
    version: str
    source_ref: str
    rebuild_with_target_gcc: bool = True


@dataclass(frozen=True)
class ToolsetConfig:
    name: str
    toolset_id: str
    root: str
    prefix: str
    runtime_strategy: str
    embed_application_runpath: bool = False
    modify_global_alternatives: bool = False
    modify_ld_so_conf: bool = False


@dataclass(frozen=True)
class SourceEntry:
    type: str
    uri: str
    sha256: str


@dataclass(frozen=True)
class PolicyConfig:
    max_transient_retries: int = 3
    max_auto_repairs: int = 2
    require_patch_approval: bool = True
    require_private_runtime_approval: bool = True
    require_publish_approval: bool = True
    fail_on_patch_fuzz: bool = True
    fail_on_system_path_write: bool = True
    fail_on_glibc_baseline_exceed: bool = True


@dataclass(frozen=True)
class JobConfig:
    schema_version: int
    name: str
    toolset_id: str
    mode: str
    platform: PlatformConfig
    base_gcc: BaseGccConfig
    target_gcc: TargetGccConfig
    binutils: BinutilsConfig
    toolset: ToolsetConfig
    sources: Dict[str, SourceEntry]
    policy: PolicyConfig
    packaging_layout: str = "recommended-closure"
    raw: Dict[str, Any] = field(default_factory=dict, compare=False)

    def canonical_json(self) -> str:
        """规范化 JSON（键排序、去除 raw），用于任务指纹计算。"""
        data = dict(self.raw)
        data.pop("_raw", None)
        return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))

    def fingerprint_component(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


def load_job_config(path: Path) -> JobConfig:
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ConfigError("E-CONFIG-PARSE", f"{path} 不是有效的 YAML 映射")
    return parse_job_config(data)


def parse_job_config(data: Dict[str, Any]) -> JobConfig:
    schema_version = _require(data, "schema_version", "顶层")
    if schema_version != SUPPORTED_SCHEMA_VERSION:
        raise ConfigError(
            "E-CONFIG-SCHEMA",
            f"不支持的 schema_version={schema_version}，当前支持 {SUPPORTED_SCHEMA_VERSION}",
        )

    job = _require(data, "job", "顶层")
    platform_raw = _require(data, "platform", "顶层")
    toolchain = _require(data, "toolchain", "顶层")
    toolset_raw = _require(data, "toolset", "顶层")
    sources_raw = _require(data, "sources", "顶层")

    distro_raw = _require(platform_raw, "distro", "platform")
    distro = DistroConfig(
        id=str(_require(distro_raw, "id", "platform.distro")),
        major=int(_require(distro_raw, "major", "platform.distro")),
    )
    if (distro.id, distro.major) not in SUPPORTED_DISTROS:
        raise ConfigError(
            "E-CONFIG-DISTRO",
            f"发行版 {distro.id} {distro.major} 不在 MVP 支持边界内（RHEL/CentOS Stream 9、10）",
        )

    architecture = str(_require(platform_raw, "architecture", "platform"))
    if architecture not in SUPPORTED_ARCHITECTURES:
        raise ConfigError(
            "E-CONFIG-ARCH",
            f"架构 {architecture} 不在 MVP 支持边界内（{', '.join(SUPPORTED_ARCHITECTURES)}）",
        )

    multilib = platform_raw.get("multilib", {}) or {}
    multilib_enabled = bool(multilib.get("enabled", False))
    if multilib_enabled:
        raise ConfigError("E-CONFIG-MULTILIB", "MVP 禁用 multilib（见方案 2.2）")

    platform = PlatformConfig(
        distro=distro,
        architecture=architecture,
        target_triple=str(_require(platform_raw, "target_triple", "platform")),
        rpm_adapter=str(_require(platform_raw, "rpm_adapter", "platform")),
        multilib_enabled=multilib_enabled,
    )

    base_gcc_raw = _require(toolchain, "base_gcc", "toolchain")
    base_gcc = BaseGccConfig(
        source=str(_require(base_gcc_raw, "source", "toolchain.base_gcc")),
        executable=str(base_gcc_raw.get("executable", "/usr/bin/gcc")),
        expected_major=(
            int(base_gcc_raw["expected_major"])
            if base_gcc_raw.get("expected_major") is not None
            else None
        ),
    )

    target_gcc_raw = _require(toolchain, "target_gcc", "toolchain")
    languages = list(_require(target_gcc_raw, "languages", "toolchain.target_gcc"))
    unsupported = set(languages) - {"c", "cxx"}
    if unsupported:
        raise ConfigError(
            "E-CONFIG-LANG",
            f"语言 {sorted(unsupported)} 不在 MVP 支持边界内（仅 c、cxx）",
        )
    target_gcc = TargetGccConfig(
        version=str(_require(target_gcc_raw, "version", "toolchain.target_gcc")),
        languages=languages,
        bootstrap=str(target_gcc_raw.get("bootstrap", "bootstrap")),
        source_ref=str(_require(target_gcc_raw, "source_ref", "toolchain.target_gcc")),
    )

    binutils_raw = _require(toolchain, "binutils", "toolchain")
    binutils = BinutilsConfig(
        version=str(_require(binutils_raw, "version", "toolchain.binutils")),
        source_ref=str(_require(binutils_raw, "source_ref", "toolchain.binutils")),
        rebuild_with_target_gcc=bool(binutils_raw.get("rebuild_with_target_gcc", True)),
    )

    toolset_id = str(_require(job, "toolset_id", "job"))
    runtime_strategy = str(_require(toolset_raw, "runtime_strategy", "toolset"))
    if runtime_strategy not in RUNTIME_STRATEGIES:
        raise ConfigError(
            "E-CONFIG-RUNTIME",
            f"runtime_strategy 必须是 {RUNTIME_STRATEGIES} 之一，收到 {runtime_strategy!r}",
        )

    toolset = ToolsetConfig(
        name=str(_require(toolset_raw, "name", "toolset")),
        toolset_id=toolset_id,
        root=str(_require(toolset_raw, "root", "toolset")),
        prefix=str(_require(toolset_raw, "prefix", "toolset")),
        runtime_strategy=runtime_strategy,
        embed_application_runpath=bool(toolset_raw.get("embed_application_runpath", False)),
        modify_global_alternatives=bool(toolset_raw.get("modify_global_alternatives", False)),
        modify_ld_so_conf=bool(toolset_raw.get("modify_ld_so_conf", False)),
    )
    if toolset.modify_global_alternatives or toolset.modify_ld_so_conf:
        raise ConfigError(
            "E-POLICY",
            "禁止修改全局 alternatives 或 /etc/ld.so.conf*（方案 10.7 硬性约束）",
        )
    if not toolset.root.startswith("/opt/"):
        raise ConfigError(
            "E-CONFIG-PREFIX",
            f"Toolset root 必须位于 /opt 之下，收到 {toolset.root!r}",
        )

    sources: Dict[str, SourceEntry] = {}
    for key in ("gcc", "binutils"):
        entry = _require(sources_raw, key, "sources")
        sources[key] = SourceEntry(
            type=str(_require(entry, "type", f"sources.{key}")),
            uri=str(_require(entry, "uri", f"sources.{key}")),
            sha256=str(_require(entry, "sha256", f"sources.{key}")),
        )

    policy_raw = data.get("policy", {}) or {}
    policy = PolicyConfig(
        max_transient_retries=int(policy_raw.get("max_transient_retries", 3)),
        max_auto_repairs=int(policy_raw.get("max_auto_repairs", 2)),
        require_patch_approval=bool(policy_raw.get("require_patch_approval", True)),
        require_private_runtime_approval=bool(
            policy_raw.get("require_private_runtime_approval", True)
        ),
        require_publish_approval=bool(policy_raw.get("require_publish_approval", True)),
        fail_on_patch_fuzz=bool(policy_raw.get("fail_on_patch_fuzz", True)),
        fail_on_system_path_write=bool(policy_raw.get("fail_on_system_path_write", True)),
        fail_on_glibc_baseline_exceed=bool(
            policy_raw.get("fail_on_glibc_baseline_exceed", True)
        ),
    )

    packaging_raw = data.get("packaging", {}) or {}
    packaging_layout = str(packaging_raw.get("layout", "recommended-closure"))
    if packaging_layout not in PACKAGING_LAYOUTS:
        raise ConfigError(
            "E-CONFIG-LAYOUT",
            f"packaging.layout 必须是 {PACKAGING_LAYOUTS} 之一，收到 {packaging_layout!r}",
        )

    return JobConfig(
        schema_version=int(schema_version),
        name=str(_require(job, "name", "job")),
        toolset_id=toolset_id,
        mode=str(job.get("mode", "qualified-build")),
        platform=platform,
        base_gcc=base_gcc,
        target_gcc=target_gcc,
        binutils=binutils,
        toolset=toolset,
        sources=sources,
        policy=policy,
        packaging_layout=packaging_layout,
        raw=data,
    )
