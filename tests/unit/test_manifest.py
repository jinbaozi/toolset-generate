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
    assert set(packages) == {"gcc", "gcc-c++", "binutils", "libstdc++-devel"}

    outputs = write_files_lists(manifest, tmp_path / "manifests")
    gcc_files = (tmp_path / "manifests" / "gcc.files").read_text()
    assert f"{PREFIX}/bin/gcc" in gcc_files
    assert "*" not in gcc_files  # 无通配符


def test_symlink_escape_blocked(tmp_path):
    stage = _make_stage(tmp_path)
    bin_dir = stage / ROOT.lstrip("/") / "usr" / "bin"
    os.symlink("/usr/bin/ld", bin_dir / "ld.bfd")  # 绝对链接逃逸 staging 根
    with pytest.raises(ManifestError) as exc:
        discover_staged_files(stage, ROOT, PREFIX, "system-nonshared")
    assert exc.value.code == "E-ISOLATION"
