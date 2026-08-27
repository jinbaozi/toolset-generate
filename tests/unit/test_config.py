import pytest

from gts_agent.core.models.config import ConfigError, parse_job_config


def test_parse_valid_config(base_config_dict):
    config = parse_job_config(base_config_dict)
    assert config.name == "test-job"
    assert config.toolset_id == "14"
    assert config.target_gcc.major == 14
    assert config.toolset.runtime_strategy == "system-nonshared"
    assert config.packaging_layout == "recommended-closure"
    assert config.fingerprint_component()  # 可计算指纹


def test_unsupported_distro_fails(base_config_dict):
    base_config_dict["platform"]["distro"] = {"id": "ubuntu", "major": 24}
    with pytest.raises(ConfigError) as exc:
        parse_job_config(base_config_dict)
    assert exc.value.code == "E-CONFIG-DISTRO"


def test_multilib_rejected_in_mvp(base_config_dict):
    base_config_dict["platform"]["multilib"] = {"enabled": True}
    with pytest.raises(ConfigError) as exc:
        parse_job_config(base_config_dict)
    assert exc.value.code == "E-CONFIG-MULTILIB"


def test_invalid_runtime_strategy(base_config_dict):
    base_config_dict["toolset"]["runtime_strategy"] = "copy-system-libs"
    with pytest.raises(ConfigError) as exc:
        parse_job_config(base_config_dict)
    assert exc.value.code == "E-CONFIG-RUNTIME"


def test_ld_so_conf_modification_rejected(base_config_dict):
    base_config_dict["toolset"]["modify_ld_so_conf"] = True
    with pytest.raises(ConfigError) as exc:
        parse_job_config(base_config_dict)
    assert exc.value.code == "E-POLICY"


def test_toolset_root_must_be_under_opt(base_config_dict):
    base_config_dict["toolset"]["root"] = "/usr/local/gts"
    base_config_dict["toolset"]["prefix"] = "/usr/local/gts/usr"
    with pytest.raises(ConfigError) as exc:
        parse_job_config(base_config_dict)
    assert exc.value.code == "E-CONFIG-PREFIX"


def test_unsupported_language_rejected(base_config_dict):
    base_config_dict["toolchain"]["target_gcc"]["languages"] = ["c", "fortran"]
    with pytest.raises(ConfigError) as exc:
        parse_job_config(base_config_dict)
    assert exc.value.code == "E-CONFIG-LANG"


def test_unsupported_architecture(base_config_dict):
    base_config_dict["platform"]["architecture"] = "riscv64"
    with pytest.raises(ConfigError) as exc:
        parse_job_config(base_config_dict)
    assert exc.value.code == "E-CONFIG-ARCH"
