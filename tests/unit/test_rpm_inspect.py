from pathlib import Path

from gts_agent.core.verify.rpm import inspectable_rpms, is_debug_or_source_rpm


def test_is_debug_or_source_rpm():
    assert is_debug_or_source_rpm(
        Path("gcc-toolset-14-binutils-debuginfo-2.41-1.el9.x86_64.rpm")
    )
    assert is_debug_or_source_rpm(
        Path("gcc-toolset-14-gcc-debugsource-14.3.0-1.el9.x86_64.rpm")
    )
    assert is_debug_or_source_rpm(Path("gcc-toolset-14-gcc-14.3.0-1.el9.src.rpm"))
    assert not is_debug_or_source_rpm(
        Path("gcc-toolset-14-gcc-14.3.0-1.el9.x86_64.rpm")
    )
    assert not is_debug_or_source_rpm(
        Path("gcc-toolset-14-runtime-14.3.0-1.el9.noarch.rpm")
    )


def test_inspectable_rpms_skips_debug_packages(tmp_path):
    (tmp_path / "gcc-toolset-14-runtime-14.3.0-1.el9.noarch.rpm").write_bytes(b"rpm")
    (tmp_path / "gcc-toolset-14-gcc-debuginfo-14.3.0-1.el9.x86_64.rpm").write_bytes(b"rpm")
    (tmp_path / "gcc-toolset-14-gcc-debugsource-14.3.0-1.el9.x86_64.rpm").write_bytes(b"rpm")
    (tmp_path / "gcc-toolset-14-gcc-14.3.0-1.el9.src.rpm").write_bytes(b"rpm")
    names = [path.name for path in inspectable_rpms(tmp_path)]
    assert names == ["gcc-toolset-14-runtime-14.3.0-1.el9.noarch.rpm"]
