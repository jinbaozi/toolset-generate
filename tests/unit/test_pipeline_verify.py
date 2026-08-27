import inspect

from gts_agent.agent.pipeline import (
    INSTALL_BINARY_RPMS,
    Pipeline,
    succeeded_report_states,
)


def test_install_binary_rpms_skips_debug_packages():
    assert "rpm -Uvh" in INSTALL_BINARY_RPMS
    assert "debuginfo" in INSTALL_BINARY_RPMS
    assert "debugsource" in INSTALL_BINARY_RPMS


def test_compile_link_test_reinstalls_rpms_in_fresh_container():
    """PodmanExecutor.run 每次都是新容器，编译阶段必须再次安装 RPM。"""
    source = inspect.getsource(Pipeline.install_and_test)
    assert source.count("INSTALL_BINARY_RPMS") >= 2
    assert "source /opt/rh/gcc-toolset-" in source


def test_abi_uses_stage_provided_glibc_nodes():
    source = inspect.getsource(Pipeline.install_and_test)
    assert "provided_glibc_nodes" in source
    assert "stage-verify.json" in source


def test_generate_rpm_parses_specs_and_srpms():
    source = inspect.getsource(Pipeline.generate_rpm)
    assert "index_spec_dir" in source
    assert "inspect_srpm_dir" in source
    assert "srpm-index.json" in source


def test_publish_report_marks_self_succeeded():
    states = [
        {"state": "IsolationTest", "status": "SUCCEEDED"},
        {"state": "PublishReport", "status": "RUNNING"},
    ]
    out = succeeded_report_states(states)
    assert out[-1] == {"state": "PublishReport", "status": "SUCCEEDED"}
    assert states[-1]["status"] == "RUNNING"


def test_publish_report_uses_succeeded_helper():
    source = inspect.getsource(Pipeline.publish_report)
    assert "succeeded_report_states" in source


def test_compile_link_test_reinstalls_rpms_in_fresh_container():
    """PodmanExecutor.run 每次都是新容器，编译阶段必须再次安装 RPM。"""
    source = inspect.getsource(Pipeline.install_and_test)
    assert source.count("INSTALL_BINARY_RPMS") >= 2
    assert "source /opt/rh/gcc-toolset-" in source


def test_abi_uses_stage_provided_glibc_nodes():
    source = inspect.getsource(Pipeline.install_and_test)
    assert "provided_glibc_nodes" in source
    assert "stage-verify.json" in source
