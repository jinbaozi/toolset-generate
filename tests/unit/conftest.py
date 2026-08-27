import copy

import pytest

BASE_CONFIG = {
    "schema_version": 1,
    "job": {"name": "test-job", "toolset_id": "14", "mode": "qualified-build"},
    "platform": {
        "distro": {"id": "centos-stream", "major": 9},
        "architecture": "x86_64",
        "target_triple": "x86_64-redhat-linux",
        "rpm_adapter": "rpm-4.16",
        "multilib": {"enabled": False},
    },
    "toolchain": {
        "base_gcc": {
            "source": "system",
            "executable": "/usr/bin/gcc",
            "expected_major": 11,
        },
        "target_gcc": {
            "version": "14.2.1",
            "languages": ["c", "cxx"],
            "bootstrap": "bootstrap",
            "source_ref": "gcc-14-redhat-profile",
        },
        "binutils": {
            "version": "2.41",
            "source_ref": "binutils-gts14-profile",
            "rebuild_with_target_gcc": True,
        },
    },
    "toolset": {
        "name": "gcc-toolset-14",
        "root": "/opt/rh/gcc-toolset-14/root",
        "prefix": "/opt/rh/gcc-toolset-14/root/usr",
        "runtime_strategy": "system-nonshared",
        "embed_application_runpath": False,
        "modify_global_alternatives": False,
        "modify_ld_so_conf": False,
    },
    "sources": {
        "gcc": {"type": "srpm", "uri": "https://example/x.src.rpm", "sha256": "a" * 64},
        "binutils": {"type": "srpm", "uri": "https://example/y.src.rpm", "sha256": "b" * 64},
    },
    "packaging": {"layout": "recommended-closure"},
    "policy": {},
}


@pytest.fixture
def base_config_dict():
    return copy.deepcopy(BASE_CONFIG)
