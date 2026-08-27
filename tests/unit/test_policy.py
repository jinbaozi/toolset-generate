from gts_agent.agent.policy_engine import (
    check_install_path,
    check_manifest_paths,
    check_provides,
    evaluate_fast_fail,
    load_policy,
)
from gts_agent.core.models.config import parse_job_config


def test_load_default_policy():
    policy = load_policy("default")
    assert policy.policy_id == "default"
    assert "/opt/rh/" in policy.allowed_install_prefixes
    assert "/usr/bin/gcc" in policy.forbidden_install_paths


def test_production_inherits_default():
    policy = load_policy("production")
    assert policy.policy_id == "production"
    assert "/opt/rh/" in policy.allowed_install_prefixes  # 继承
    assert policy.data["release_gates"]["require_sbom"] is True


def test_toolset_path_allowed():
    policy = load_policy("default")
    decision = check_install_path(
        policy, "/opt/rh/gcc-toolset-14/root/usr/bin/gcc"
    )
    assert decision.result == "ALLOW"


def test_env_wrapper_dir_allowed_without_trailing_slash():
    """runtime 包拥有 %dir /usr/lib/gcc-toolset，前缀配置带尾斜杠时也必须允许该目录。"""
    policy = load_policy("default")
    assert check_install_path(policy, "/usr/lib/gcc-toolset").result == "ALLOW"
    assert check_install_path(
        policy, "/usr/lib/gcc-toolset/14-env.source"
    ).result == "ALLOW"
    assert check_install_path(
        policy, "/usr/bin/gcc-toolset-14-env"
    ).result == "ALLOW"


def test_elf_build_id_index_allowed():
    """rpm 写入的 /usr/lib/.build-id 不覆盖系统编译器。"""
    policy = load_policy("default")
    assert check_install_path(policy, "/usr/lib/.build-id").result == "ALLOW"
    assert check_install_path(
        policy, "/usr/lib/.build-id/0d/70a6e847d69e62db63029e46cedbbba8abedd6"
    ).result == "ALLOW"


def test_system_gcc_denied():
    policy = load_policy("default")
    decision = check_install_path(policy, "/usr/bin/gcc")
    assert decision.result == "DENY"


def test_ld_so_conf_denied():
    policy = load_policy("default")
    violations = check_manifest_paths(policy, [
        "/opt/rh/gcc-toolset-14/root/usr/bin/g++",
        "/etc/ld.so.conf.d/gts14.conf",
        "/usr/lib64/libstdc++.so.6",
    ])
    assert len(violations) == 2


def test_forbidden_provides():
    policy = load_policy("default")
    violations = check_provides(policy, [
        "gcc-toolset(14)",
        "gcc",
        "gcc-toolset-gcc(major) = 14",
    ])
    assert len(violations) == 1
    assert "gcc" in violations[0].detail


def test_fast_fail_private_runtime_requires_approval(base_config_dict):
    base_config_dict["toolset"]["runtime_strategy"] = "private-runtime"
    config = parse_job_config(base_config_dict)
    decisions = evaluate_fast_fail(config)
    assert any(d.result == "APPROVAL_REQUIRED" for d in decisions)


def test_fast_fail_missing_sha256(base_config_dict):
    base_config_dict["sources"]["gcc"]["sha256"] = "<required>"
    config = parse_job_config(base_config_dict)
    decisions = evaluate_fast_fail(config)
    assert any(
        d.result == "DENY" and "sha256" in d.detail for d in decisions
    )
