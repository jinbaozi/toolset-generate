from pathlib import Path

from gts_agent.core.models.config import parse_job_config
import yaml


def test_rhel9_1430_example_parses():
    path = Path(__file__).resolve().parents[2] / "examples" / "rhel9-gts14.3.0.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    config = parse_job_config(data)
    assert config.platform.distro.id == "rhel"
    assert config.target_gcc.version == "14.3.0"
    assert config.toolset.runtime_strategy == "private-runtime"
    assert config.build_executor == "podman"
    assert config.platform.glibc_baseline == "2.34"
    assert config.packaging_layout == "recommended-closure"
