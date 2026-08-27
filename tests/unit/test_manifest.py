import os

import pytest

from gts_agent.core.manifest.discover import (
    ManifestError,
    classify_path,
    discover_staged_files,
    write_files_lists,
)

PREFIX = "/opt/rh/gcc-toolset-14/root/usr"
ROOT = "/opt/rh/gcc-toolset-14/root"


def test_classify_core_paths():
    assert classify_path(f"{PREFIX}/bin/gcc", PREFIX, "system-nonshared") == "gcc"
    assert classify_path(f"{PREFIX}/bin/g++", PREFIX, "system-nonshared") == "gcc-c++"
    assert classify_path(f"{PREFIX}/bin/ld", PREFIX, "system-nonshared") == "binutils"
    assert classify_path(f"{PREFIX}/bin/readelf", PREFIX, "system-nonshared") == "binutils"
    assert classify_path(
        f"{PREFIX}/libexec/gcc/x86_64-redhat-linux/14/cc1plus",
        PREFIX, "system-nonshared",
    ) == "gcc-c++"
    assert classify_path(
        f"{PREFIX}/include/c++/14/vector", PREFIX, "system-nonshared"
    ) == "libstdc++-devel"
    assert classify_path(
        f"{PREFIX}/lib/gcc/x86_64-redhat-linux/14/libgcc.a",
        PREFIX, "system-nonshared",
    ) == "gcc"


def test_nonshared_classification():
    path = f"{PREFIX}/lib/gcc/x86_64-redhat-linux/14/libstdc++_nonshared.a"
    assert classify_path(path, PREFIX, "system-nonshared") == "libstdc++-devel"
    with pytest.raises(ManifestError) as exc:
        classify_path(path, PREFIX, "private-runtime")
    assert exc.value.code == "E-NONSHARED-MISMATCH"


def test_private_dso_classification():
    dso = f"{PREFIX}/lib64/libstdc++.so.6.0.33"
    assert classify_path(dso, PREFIX, "private-runtime") == "runtime-libs"
    with pytest.raises(ManifestError) as exc:
        classify_path(dso, PREFIX, "system-nonshared")
    assert exc.value.code == "E-MANIFEST"


def test_unversioned_libgcc_s_is_runtime_libs():
    assert classify_path(
        f"{PREFIX}/lib64/libgcc_s.so", PREFIX, "private-runtime"
    ) == "runtime-libs"


def test_tooldir_binutils_vs_gcc_libs():
    triple = f"{PREFIX}/x86_64-redhat-linux"
    assert classify_path(f"{triple}/bin/as", PREFIX, "private-runtime") == "binutils"
    assert classify_path(
        f"{triple}/lib/ldscripts/elf_x86_64.x", PREFIX, "private-runtime"
    ) == "binutils"
    assert classify_path(
        f"{triple}/lib/libgcc_s.so.1", PREFIX, "private-runtime"
    ) == "runtime-libs"


def _make_stage(tmp_path):
    prefix_dir = tmp_path / ROOT.lstrip("/") / "usr"
    (prefix_dir / "bin").mkdir(parents=True)
    (prefix_dir / "bin" / "gcc").write_bytes(b"\x7fELF-fake")
    (prefix_dir / "bin" / "g++").write_bytes(b"\x7fELF-fake")
    (prefix_dir / "bin" / "ld").write_bytes(b"\x7fELF-fake")
    include_dir = prefix_dir / "include" / "c++" / "14"
    include_dir.mkdir(parents=True)
    (include_dir / "vector").write_text("// header")
    return tmp_path


def test_discover_and_write_lists(tmp_path):
    stage = _make_stage(tmp_path)
    manifest = discover_staged_files(stage, ROOT, PREFIX, "system-nonshared")
    packages = manifest.by_package()
    # gcc 构建根中的 binutils 工具从 staging 删除，避免未打包文件和 RPM 冲突
    assert set(packages) == {"gcc", "gcc-c++", "libstdc++-devel"}
    assert not (stage / ROOT.lstrip("/") / "usr" / "bin" / "ld").exists()

    outputs = write_files_lists(
        manifest, tmp_path / "manifests",
        required_packages=["gcc", "gcc-c++", "libstdc++-devel"],
    )
    gcc_files = (tmp_path / "manifests" / "gcc.files").read_text()
    assert f"{PREFIX}/bin/gcc" in gcc_files
    assert "*" not in gcc_files  # 无通配符
    assert "gcc-c++.files" in {path.name for path in outputs.values()}


def test_write_files_lists_emits_required_empty_packages(tmp_path):
    stage = _make_stage(tmp_path)
    (stage / ROOT.lstrip("/") / "usr" / "bin" / "ld").unlink()
    manifest = discover_staged_files(stage, ROOT, PREFIX, "system-nonshared")
    outputs = write_files_lists(
        manifest,
        tmp_path / "manifests",
        required_packages=["gcc", "gcc-c++", "libstdc++-devel", "runtime-libs"],
    )
    runtime_list = tmp_path / "manifests" / "runtime-libs.files"
    assert runtime_list.exists()
    assert runtime_list.read_text() == ""
    assert "runtime-libs" in outputs


def test_gcc_prunes_tooldir_binutils_copies(tmp_path):
    stage = _make_stage(tmp_path)
    tooldir = stage / ROOT.lstrip("/") / "usr" / "x86_64-redhat-linux"
    (tooldir / "bin").mkdir(parents=True)
    (tooldir / "lib" / "ldscripts").mkdir(parents=True)
    (tooldir / "bin" / "as").write_bytes(b"\x7fELF-fake")
    (tooldir / "lib" / "ldscripts" / "elf_x86_64.x").write_text("SECTIONS {}")
    (tooldir / "lib" / "libgcc_s.so.1").write_bytes(b"\x7fELF-fake")
    manifest = discover_staged_files(stage, ROOT, PREFIX, "private-runtime")
    packages = manifest.by_package()
    assert "binutils" not in packages
    assert not (tooldir / "bin" / "as").exists()
    assert not (tooldir / "lib" / "ldscripts" / "elf_x86_64.x").exists()
    assert (tooldir / "lib" / "libgcc_s.so.1").exists()
    assert any(
        entry.path.endswith("libgcc_s.so.1") and entry.package == "runtime-libs"
        for entry in manifest.entries
    )


def test_private_runtime_requires_dsos(tmp_path):
    stage = _make_stage(tmp_path)
    with pytest.raises(ManifestError) as exc:
        discover_staged_files(stage, ROOT, PREFIX, "private-runtime")
    assert exc.value.code == "E-MANIFEST"


def test_symlink_escape_blocked(tmp_path):
    stage = _make_stage(tmp_path)
    bin_dir = stage / ROOT.lstrip("/") / "usr" / "bin"
    os.symlink("/usr/bin/ld", bin_dir / "ld.bfd")  # 绝对链接逃逸 staging 根
    with pytest.raises(ManifestError) as exc:
        discover_staged_files(stage, ROOT, PREFIX, "system-nonshared")
    assert exc.value.code == "E-ISOLATION"
