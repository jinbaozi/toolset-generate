"""策略引擎：加载策略 YAML，评估快速失败规则和安装路径边界。

方案 2.3 的快速失败条件与方案 10.7 的系统路径保护在此集中实现。
策略是数据（policies/*.yaml），引擎只做确定性评估。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from gts_agent.core.models.config import JobConfig

_POLICY_DIR = Path(__file__).resolve().parent.parent / "policies"


class PolicyViolation(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code


@dataclass
class PolicyDecision:
    rule: str
    result: str          # ALLOW / DENY / APPROVAL_REQUIRED
    detail: str = ""


@dataclass
class Policy:
    policy_id: str
    data: Dict[str, Any] = field(default_factory=dict)

    @property
    def allowed_install_prefixes(self) -> List[str]:
        return list(self.data.get("allowed_install_prefixes", []))

    @property
    def forbidden_install_paths(self) -> List[str]:
        return list(self.data.get("forbidden_install_paths", []))

    @property
    def forbidden_provides(self) -> List[str]:
        return list(self.data.get("forbidden_provides", []))


def load_policy(name: str = "default", policy_dir: Optional[Path] = None) -> Policy:
    directory = policy_dir or _POLICY_DIR
    path = directory / f"{name}.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if data.get("inherits"):
        base = load_policy(str(data["inherits"]), directory)
        merged = dict(base.data)
        for key, value in data.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = {**merged[key], **value}
            else:
                merged[key] = value
        data = merged
    return Policy(policy_id=str(data.get("policy_id", name)), data=data)


def check_install_path(policy: Policy, install_path: str) -> PolicyDecision:
    """校验单个安装路径。命中禁止路径 => DENY；不在允许前缀 => DENY。"""
    for forbidden in policy.forbidden_install_paths:
        if install_path == forbidden or install_path.startswith(forbidden.rstrip("/") + "/"):
            return PolicyDecision(
                rule="forbidden_install_paths",
                result="DENY",
                detail=f"{install_path} 命中禁止路径 {forbidden}（覆盖系统工具）",
            )
    for prefix in policy.allowed_install_prefixes:
        if _matches_allowed_prefix(install_path, prefix):
            return PolicyDecision(rule="allowed_install_prefixes", result="ALLOW")
    return PolicyDecision(
        rule="allowed_install_prefixes",
        result="DENY",
        detail=f"{install_path} 不在任何允许的安装前缀内",
    )


def _matches_allowed_prefix(install_path: str, prefix: str) -> bool:
    if install_path.startswith(prefix):
        return True
    stripped = prefix.rstrip("/")
    return bool(stripped) and install_path == stripped


def check_manifest_paths(policy: Policy, install_paths: List[str]) -> List[PolicyDecision]:
    violations = []
    for path in install_paths:
        decision = check_install_path(policy, path)
        if decision.result != "ALLOW":
            violations.append(decision)
    return violations


def check_provides(policy: Policy, provides: List[str]) -> List[PolicyDecision]:
    """禁止提供无版本的系统 capability（如裸 'gcc'）。"""
    violations = []
    for provide in provides:
        bare_name = provide.split("=")[0].split("(")[0].strip()
        if bare_name in policy.forbidden_provides:
            violations.append(PolicyDecision(
                rule="forbidden_provides",
                result="DENY",
                detail=f"Provides: {provide!r} 会错误满足系统编译器依赖",
            ))
    return violations


def evaluate_fast_fail(config: JobConfig) -> List[PolicyDecision]:
    """配置级快速失败规则（方案 2.3 中在配置阶段即可判定的部分）。"""
    decisions: List[PolicyDecision] = []

    if config.platform.multilib_enabled:
        decisions.append(PolicyDecision(
            "multilib-disabled-in-mvp", "DENY", "MVP 禁用 multilib"))

    if config.toolset.modify_ld_so_conf or config.toolset.modify_global_alternatives:
        decisions.append(PolicyDecision(
            "no-global-loader-changes", "DENY",
            "禁止修改 /etc/ld.so.conf* 或全局 alternatives"))

    if config.toolset.runtime_strategy == "private-runtime":
        decisions.append(PolicyDecision(
            "private-runtime-approval", "APPROVAL_REQUIRED",
            "private-runtime 引入必须人工审批"))

    for name, source in config.sources.items():
        if not source.sha256 or source.sha256 == "<required>":
            decisions.append(PolicyDecision(
                "source-sha256-required", "DENY",
                f"sources.{name} 缺少 sha256（所有源码输入必须锁定哈希）"))

    return decisions
