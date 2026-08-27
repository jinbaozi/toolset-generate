import pytest

from gts_agent.core.compatibility.glibc import (
    check_glibc_baseline,
    parse_glibc_version_node,
)
from gts_agent.core.models.compatibility import Verdict


def test_parse_version_node():
    assert parse_glibc_version_node("GLIBC_2.34") == (2, 34)
    assert parse_glibc_version_node("GLIBC_2.2.5") == (2, 2, 5)
    with pytest.raises(ValueError):
        parse_glibc_version_node("GLIBCXX_3.4")


def test_baseline_pass():
    report = check_glibc_baseline({"GLIBC_2.17", "GLIBC_2.34"}, "2.34")
    assert report.verdict == Verdict.PASS


def test_baseline_exceeded_fails():
    report = check_glibc_baseline({"GLIBC_2.38", "GLIBC_2.34"}, "2.34")
    assert report.verdict == Verdict.FAIL
    assert "E-GLIBC-BASELINE" in report.reason_codes
    finding = report.findings[0]
    assert "GLIBC_2.38" in finding.facts["required"]
    assert "copy_newer_glibc_into_toolset" in finding.forbidden_actions


def test_glibc_private_always_fails():
    report = check_glibc_baseline({"GLIBC_PRIVATE"}, "2.34")
    assert report.verdict == Verdict.FAIL


def test_non_glibc_nodes_ignored():
    report = check_glibc_baseline({"GLIBCXX_3.4.30", "CXXABI_1.3"}, "2.34")
    assert report.verdict == Verdict.PASS


def test_rhel_backported_nodes_pass_when_libc_provides_them():
    """RHEL 9 glibc 软件包版本仍是 2.34，但会回移植 GLIBC_2.35 等节点。"""
    report = check_glibc_baseline(
        {"GLIBC_2.34", "GLIBC_2.35"},
        "2.34",
        provided_nodes={"GLIBC_2.34", "GLIBC_2.35", "GLIBC_2.2.5"},
    )
    assert report.verdict == Verdict.PASS


def test_provided_nodes_still_fail_for_missing_versions():
    report = check_glibc_baseline(
        {"GLIBC_2.38"},
        "2.34",
        provided_nodes={"GLIBC_2.34", "GLIBC_2.35"},
    )
    assert report.verdict == Verdict.FAIL
    assert "GLIBC_2.38" in report.findings[0].facts["required"]
