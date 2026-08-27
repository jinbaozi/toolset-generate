from gts_agent.core.compatibility.gcc import analyze_gcc, minimum_base_major
from gts_agent.core.models.compatibility import Verdict
from gts_agent.core.models.config import parse_job_config
from gts_agent.core.models.inventory import GccInfo, Inventory


def _inventory(major=11, dumpmachine="x86_64-redhat-linux"):
    inv = Inventory()
    inv.gcc = GccInfo(
        executable="/usr/bin/gcc",
        version=f"gcc (GCC) {major}.4.1",
        major=major,
        dumpmachine=dumpmachine,
    )
    return inv


def test_minimum_base_major():
    assert minimum_base_major(15) == 6
    assert minimum_base_major(14) == 5
    assert minimum_base_major(4) == 4


def test_pass_case(base_config_dict):
    config = parse_job_config(base_config_dict)
    report = analyze_gcc(config, _inventory(major=11))
    assert report.verdict == Verdict.PASS


def test_triple_mismatch_fails(base_config_dict):
    config = parse_job_config(base_config_dict)
    report = analyze_gcc(config, _inventory(dumpmachine="aarch64-redhat-linux"))
    assert report.verdict == Verdict.FAIL
    assert "E-TRIPLE-MISMATCH" in report.reason_codes


def test_bootstrap_insufficient_fails(base_config_dict):
    base_config_dict["toolchain"]["base_gcc"]["expected_major"] = 4
    config = parse_job_config(base_config_dict)
    report = analyze_gcc(config, _inventory(major=4))
    assert report.verdict == Verdict.FAIL
    assert "E-BOOTSTRAP" in report.reason_codes


def test_unexpected_base_major_fails(base_config_dict):
    config = parse_job_config(base_config_dict)  # expected_major=11
    report = analyze_gcc(config, _inventory(major=13))
    assert "E-BASE-GCC-UNEXPECTED" in report.reason_codes


def test_target_older_than_base_warns(base_config_dict):
    base_config_dict["toolchain"]["base_gcc"]["expected_major"] = 15
    base_config_dict["toolchain"]["target_gcc"]["version"] = "14.2.1"
    config = parse_job_config(base_config_dict)
    report = analyze_gcc(config, _inventory(major=15))
    assert report.verdict == Verdict.WARN
    assert "W-TARGET-OLDER-THAN-BASE" in report.reason_codes
