import json
from pathlib import Path

from gts_agent.core.sources.patches import apply_patch, apply_patch_manifest
from gts_agent.core.sources.srpm import parse_spec_text
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
