"""RPM 元数据与路径策略检查（方案 17.2）。"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Dict, List

from gts_agent.agent.policy_engine import Policy, check_install_path, check_provides


def _rpm_query(rpm: Path, query: str) -> str:
    result = subprocess.run(
        ["rpm", "-qp", "--qf", query, str(rpm)],
        capture_output=True, text=True, timeout=60, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"rpm -qp {rpm} 失败: {result.stderr.strip()[:300]}")
    return result.stdout.strip()


def inspect_rpm(rpm: Path, policy: Policy) -> Dict[str, object]:
    files = subprocess.run(
        ["rpm", "-qpl", str(rpm)], capture_output=True, text=True,
        timeout=60, check=False,
    ).stdout.splitlines()
    provides = subprocess.run(
        ["rpm", "-qp", "--provides", str(rpm)], capture_output=True, text=True,
        timeout=60, check=False,
    ).stdout.splitlines()
    requires = subprocess.run(
        ["rpm", "-qpR", str(rpm)], capture_output=True, text=True,
        timeout=60, check=False,
    ).stdout.splitlines()

    path_violations = [
        {"path": path, "detail": decision.detail}
        for path in files
        for decision in [check_install_path(policy, path)]
        if decision.result != "ALLOW" and not path.startswith("%dir")
    ]
    # rpm -qpl 不会带 %dir 前缀；目录也必须在允许前缀内
    provide_violations = [
        {"provide": item.detail}
        for item in check_provides(policy, [p.split()[0] for p in provides if p])
    ]
    return {
        "rpm": str(rpm),
        "name": _rpm_query(rpm, "%{NAME}"),
        "nevra": _rpm_query(rpm, "%{NAME}-%{VERSION}-%{RELEASE}.%{ARCH}"),
        "files": files,
        "provides": provides,
        "requires": requires,
        "path_violations": path_violations,
        "provide_violations": provide_violations,
        "passed": not path_violations and not provide_violations,
    }


def inspect_rpm_dir(rpms_dir: Path, policy: Policy) -> Dict[str, object]:
    reports = [
        inspect_rpm(path, policy)
        for path in sorted(rpms_dir.rglob("*.rpm"))
        if not path.name.endswith(".src.rpm")
    ]
    return {
        "passed": all(item["passed"] for item in reports) if reports else False,
        "packages": reports,
    }
