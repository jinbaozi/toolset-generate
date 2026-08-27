"""Orchestrator 端到端（宿主非 RHEL 时通过注入 inventory 模拟）。"""

import json

import pytest
import yaml

from gts_agent.agent.approvals import record_approval
from gts_agent.agent.orchestrator import Orchestrator
from gts_agent.core.models.config import parse_job_config

FAKE_INVENTORY = {
    "os_id": "centos", "os_version_id": "9", "architecture": "x86_64",
    "kernel": "5.14.0", "rpm_version": "4.16.1.3",
    "rpm_target_platform": "x86_64-redhat-linux", "rpm_libdir": "/usr/lib64",
    "glibc_version": "2.34",
    "gcc": {
        "executable": "/usr/bin/gcc",
        "version": "gcc (GCC) 11.4.1",
        "major": 11,
        "dumpmachine": "x86_64-redhat-linux",
        "search_dirs": "",
    },
    "binutils": {"ld_version": "GNU ld 2.35", "as_version": "GNU as 2.35"},
    "cpu_count": 8, "memory_gib": 16.0, "warnings": [],
}


@pytest.fixture
def orchestrator(base_config_dict, tmp_path):
    config = parse_job_config(base_config_dict)
    orch = Orchestrator(config, work_root=tmp_path / "work")
    # 注入伪造 inventory 并直接标记 Discover 成功（避免依赖宿主 RHEL 环境）
    (orch.job_dir / "inventory.json").write_text(
        json.dumps(FAKE_INVENTORY), encoding="utf-8"
    )
    orch.machine.run_state(
        __import__("gts_agent.agent.state_machine", fromlist=["State"]).State.DISCOVER,
        {"injected": True},
        lambda _x: (FAKE_INVENTORY, {}),
    )
    return orch


def test_full_flow_to_plan(orchestrator):
    orch = orchestrator
    lock = orch.resolve_sources()
    assert len(lock.sources) == 2

    report = orch.analyze(run_binutils_probes=False)
    assert report["verdict"] in ("PASS", "WARN")

    plan_path = orch.generate_plan()
    plan = yaml.safe_load(plan_path.read_text(encoding="utf-8"))
    assert plan["adapter"] == "rhel9"
    assert plan["nonshared_baseline"] == "110"
    assert "gcc-toolset-14-libstdc++-devel" in plan["packages"]
    assert "--disable-multilib" in plan["gcc"]["configure_flags"]
    assert plan["build_dag"][0] == "runtime-macro-package"

    specs_dir = orch.job_dir / "specs"
    assert (specs_dir / "gcc-toolset-14-gcc.spec").exists()
    assert (specs_dir / "gcc-toolset-14-binutils.spec").exists()
    assert (specs_dir / "gcc-toolset-14-runtime.spec").exists()

    # 未审批时构建门必须阻断
    with pytest.raises(Exception):
        orch.check_build_gate()

    record_approval(
        orch.job_dir, orch.config.name, orch.plan_sha256(),
        "approve", "release-engineer",
    )
    orch.check_build_gate()  # 不再抛异常

    assert orch.fingerprint().startswith("sha256:")


def test_private_runtime_needs_extra_approval(base_config_dict, tmp_path):
    base_config_dict["toolset"]["runtime_strategy"] = "private-runtime"
    base_config_dict["job"]["name"] = "private-runtime-job"
    config = parse_job_config(base_config_dict)
    orch = Orchestrator(config, work_root=tmp_path / "work")
    (orch.job_dir / "inventory.json").write_text(
        json.dumps(FAKE_INVENTORY), encoding="utf-8"
    )
    from gts_agent.agent.state_machine import State
    orch.machine.run_state(State.DISCOVER, {}, lambda _x: (FAKE_INVENTORY, {}))
    orch.resolve_sources()
    orch.analyze(run_binutils_probes=False)
    plan_path = orch.generate_plan()
    plan = yaml.safe_load(plan_path.read_text(encoding="utf-8"))
    assert "gcc-toolset-14-runtime-libs" in plan["packages"]
    assert plan["nonshared_baseline"] is None

    record_approval(
        orch.job_dir, config.name, orch.plan_sha256(), "approve", "eng",
    )
    # 只有 build-plan 审批仍不够：private-runtime 需要单独审批
    with pytest.raises(Exception):
        orch.check_build_gate()
    record_approval(
        orch.job_dir, config.name, orch.plan_sha256(), "approve", "eng",
        scope="private-runtime",
    )
    orch.check_build_gate()
