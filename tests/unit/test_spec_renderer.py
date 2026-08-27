from pathlib import Path

import pytest

from gts_agent.core.spec.renderer import (
    SpecRenderError,
    render_template,
    render_template_file,
)

TEMPLATE_DIR = (
    Path(__file__).resolve().parents[2] / "gts_agent" / "templates" / "spec"
)


def test_render_simple():
    assert render_template("Name: @NAME@", {"NAME": "gcc"}) == "Name: gcc"


def test_missing_token_fails():
    with pytest.raises(SpecRenderError):
        render_template("Name: @NAME@ @OTHER@", {"NAME": "gcc"})


def test_forbidden_glob_fails():
    with pytest.raises(SpecRenderError) as exc:
        render_template("%files\n%{_bindir}/*\n", {})
    assert "通配符" in str(exc.value)


def _gcc_tokens():
    return {
        "TOOLSET_ID": "14",
        "GCC_MAJOR": "14",
        "GCC_VERSION": "14.2.1",
        "TARGET_TRIPLE": "x86_64-redhat-linux",
        "RUNTIME_STRATEGY": "system-nonshared",
        "RELEASE": "1",
        "LICENSE_EXPRESSION": "GPL-3.0-or-later WITH GCC-exception-3.1",
        "PROJECT_URL": "https://example.internal",
        "SOURCE_VERSION": "14.2.1",
        "SOURCE_DIR": "gcc-14.2.1",
        "LANGUAGES": "c,c++",
        "CONFIGURE_FLAGS": "--disable-multilib",
        "GCC_CONFIGURE_FLAGS": "--disable-multilib",
        "BINUTILS_CONFIGURE_FLAGS": "",
        "BOOTSTRAP_TARGET": "bootstrap",
        "VALIDATION_PROFILE": "production",
        "RUNTIME_EVR": "14.2.1-1",
        "BINUTILS_EVR": "2.41-1",
        "BINUTILS_MIN_EVR": "2.41",
        "BINUTILS_VERSION": "2.41",
        "RUNTIME_VERSION": "14.2.1",
        "LIB_NAME": "lib64",
        "GLIBC_BASELINE": "2.34",
        "CHANGELOG": "* Thu Aug 27 2026 GCC Toolset Agent <gts-agent@example.internal> - 1\n- initial",
    }


def test_render_gcc_template():
    rendered = render_template_file(TEMPLATE_DIR / "gcc.spec.in", _gcc_tokens())
    assert "Name:           %{tsname}-gcc" in rendered
    assert "%files -f %{_builddir}/manifests/gcc.files" in rendered
    leftover = [token for token in rendered.split() if token.startswith("@") and token.endswith("@")]
    assert leftover == []
    assert "Provides:       gcc-toolset(%{toolset_id})" in rendered
    # 不提供裸 gcc capability
    assert "\nProvides:       gcc\n" not in rendered


def test_render_binutils_template():
    rendered = render_template_file(TEMPLATE_DIR / "binutils.spec.in", _gcc_tokens())
    assert "--disable-gold" in rendered
    assert "--enable-new-dtags" in rendered


def test_render_runtime_template():
    rendered = render_template_file(TEMPLATE_DIR / "runtime.spec.in", _gcc_tokens())
    assert "BuildArch:      noarch" in rendered
