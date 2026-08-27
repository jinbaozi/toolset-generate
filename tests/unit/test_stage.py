from pathlib import Path

from gts_agent.core.verify.stage import verify_stage


def _stage_with_gcc(tmp_path: Path) -> Path:
    prefix = tmp_path / "opt/rh/gcc-toolset-14/root/usr"
    bindir = prefix / "bin"
    bindir.mkdir(parents=True)
    gcc = bindir / "gcc"
    gcc.write_bytes(b"not-elf")
    return tmp_path


def test_verify_ignores_python_prettyprinter_buildroot_string(tmp_path):
    stage = _stage_with_gcc(tmp_path)
    py = (
        stage / "opt/rh/gcc-toolset-14/root/usr/share/gcc-14/python"
        / "libstdcxx" / "v6" / "printers.py"
    )
    py.parent.mkdir(parents=True)
    py.write_text(
        f"# generated in {stage / 'BUILDROOT-not-used'} and also {tmp_path}\n"
        "class Printer:\n    pass\n"
    )
    result = verify_stage(
        stage, "/opt/rh/gcc-toolset-14/root", "2.34", check_buildroot_leak=True
    )
    assert result.passed
    assert result.buildroot_leaks == []


def test_verify_flags_linker_script_buildroot_leak(tmp_path):
    stage = _stage_with_gcc(tmp_path)
    script = stage / "opt/rh/gcc-toolset-14/root/usr/lib64" / "libstdc++.so"
    script.parent.mkdir(parents=True)
    script.write_text(f"INPUT ( {tmp_path}/hidden/libstdc++.so.6 )\n")
    result = verify_stage(
        stage, "/opt/rh/gcc-toolset-14/root", "2.34", check_buildroot_leak=True
    )
    assert not result.passed
    assert any("libstdc++.so" in item for item in result.buildroot_leaks)
