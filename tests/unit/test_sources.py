from pathlib import Path
from unittest.mock import MagicMock

import pytest

from gts_agent.core.sources.patches import (
    PatchError,
    apply_patch,
    apply_patch_manifest,
)
from gts_agent.core.sources.srpm import (
    SrpmError,
    expand_simple_macros,
    index_spec_dir,
    inspect_srpm_dir,
    parse_spec_text,
    query_srpm,
)
from gts_agent.core.sources.tarball import fetch_tarball, lock_local_source


def test_lock_and_reuse_tarball(tmp_path):
    payload = b"gts-source-bytes"
    src = tmp_path / "gcc-14.3.0.tar.xz"
    src.write_bytes(payload)
    digest = lock_local_source(src)
    cache = tmp_path / "cache"
    copied = fetch_tarball(str(src), cache, digest)
    assert copied.exists()
    assert copied.read_bytes() == payload
    # 第二次走缓存
    again = fetch_tarball(str(src), cache, digest)
    assert again == copied


def test_parse_spec_extracts_patches_and_subpackages():
    spec = parse_spec_text("""
Name: gcc-toolset-14-gcc
Version: 14.3.0
Release: 1
%global toolset_id 14
Source0: gcc-14.3.0.tar.xz
Patch0: gcc14-libstdc++-compat.patch
BuildRequires: make gcc
%package c++
%package -n gcc-toolset-14-libstdc++-devel
%files
%files c++
""")
    assert spec.name == "gcc-toolset-14-gcc"
    assert spec.version == "14.3.0"
    assert spec.patches[0].filename == "gcc14-libstdc++-compat.patch"
    assert spec.subpackages[0].name == "c++"
    assert spec.subpackages[1].is_prefixed
    assert spec.globals["toolset_id"] == "14"


def test_zero_fuzz_patch(tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    (source / "file.c").write_text("int x = 1;\n", encoding="utf-8")
    patch = tmp_path / "change.patch"
    patch.write_text(
        "--- a/file.c\n+++ b/file.c\n@@ -1 +1 @@\n-int x = 1;\n+int x = 2;\n",
        encoding="utf-8",
    )
    application = apply_patch(patch, source, strip=1, patch_id="t1")
    assert application.applied
    assert (source / "file.c").read_text(encoding="utf-8") == "int x = 2;\n"

    manifest = tmp_path / "manifest.yaml"
    manifest.write_text("patches: []\n", encoding="utf-8")
    report = apply_patch_manifest(manifest, source)
    assert report.applications == []


def test_zero_fuzz_rejects_context_mismatch(tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    (source / "file.c").write_text("int x = 99;\n", encoding="utf-8")
    patch = tmp_path / "change.patch"
    patch.write_text(
        "--- a/file.c\n+++ b/file.c\n@@ -1 +1 @@\n-int x = 1;\n+int x = 2;\n",
        encoding="utf-8",
    )
    with pytest.raises(PatchError) as exc:
        apply_patch(patch, source, strip=1, patch_id="t1")
    assert "E-PATCH" in str(exc.value)


def test_manifest_rejects_nonzero_fuzz(tmp_path):
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        "patches:\n  - id: x\n    source_file: a.patch\n    sha256: ''\n"
        "    fuzz_allowed: 1\n",
        encoding="utf-8",
    )
    with pytest.raises(PatchError) as exc:
        apply_patch_manifest(manifest, tmp_path)
    assert "E-POLICY" in str(exc.value)


def test_expand_simple_macros_resolves_nested_globals():
    expanded = expand_simple_macros(
        "%{tsname}-gcc",
        {"toolset_id": "14", "tsname": "gcc-toolset-%{toolset_id}"},
    )
    assert expanded == "gcc-toolset-14-gcc"


def test_query_srpm_requires_rpm(monkeypatch, tmp_path):
    monkeypatch.setattr("gts_agent.core.sources.srpm.shutil.which", lambda _name: None)
    with pytest.raises(SrpmError, match="缺少 rpm"):
        query_srpm(tmp_path / "missing.src.rpm")


def test_query_srpm_parses_qf(monkeypatch, tmp_path):
    srpm = tmp_path / "pkg.src.rpm"
    srpm.write_bytes(b"rpm")
    monkeypatch.setattr(
        "gts_agent.core.sources.srpm.shutil.which", lambda name: f"/usr/bin/{name}"
    )

    def fake_run(*_args, **_kwargs):
        result = MagicMock()
        result.returncode = 0
        result.stdout = (
            "NAME=gcc-toolset-14-gcc\nVERSION=14.3.0\nRELEASE=1.el9\nARCH=src\n"
        )
        result.stderr = ""
        return result

    monkeypatch.setattr("gts_agent.core.sources.srpm.subprocess.run", fake_run)
    meta = query_srpm(srpm)
    assert meta["NAME"] == "gcc-toolset-14-gcc"
    assert meta["VERSION"] == "14.3.0"


def test_index_spec_dir_and_empty_srpm_dir(tmp_path):
    spec = tmp_path / "gcc-toolset-14-runtime.spec"
    spec.write_text(
        "Name: gcc-toolset-14-runtime\nVersion: 14.3.0\nRelease: 1\n"
        "Source0: enable.in\nPatch0: unused.patch\n"
        "%package -n gcc-toolset-14-runtime-libs\n%files\n",
        encoding="utf-8",
    )
    indexed = index_spec_dir(tmp_path)
    assert indexed[0]["name"] == "gcc-toolset-14-runtime"
    assert indexed[0]["name_expanded"] == "gcc-toolset-14-runtime"
    assert indexed[0]["patches"][0]["filename"] == "unused.patch"
    assert indexed[0]["subpackages"][0]["is_prefixed"]
    empty = inspect_srpm_dir(tmp_path / "missing-srpms", tmp_path / "extract")
    assert empty["passed"] is True
    assert empty["srpms"] == []
