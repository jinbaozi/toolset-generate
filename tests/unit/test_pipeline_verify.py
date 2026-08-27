import inspect

from gts_agent.agent.pipeline import INSTALL_BINARY_RPMS, Pipeline


def test_install_binary_rpms_skips_debug_packages():
    assert "rpm -Uvh" in INSTALL_BINARY_RPMS
    assert "debuginfo" in INSTALL_BINARY_RPMS
    assert "debugsource" in INSTALL_BINARY_RPMS


def test_compile_link_test_reinstalls_rpms_in_fresh_container():
    """PodmanExecutor.run 每次都是新容器，编译阶段必须再次安装 RPM。"""
    source = inspect.getsource(Pipeline.install_and_test)
    assert source.count("INSTALL_BINARY_RPMS") >= 2
    assert "source /opt/rh/gcc-toolset-" in source
