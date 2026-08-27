from pathlib import Path

from gts_agent.executors.podman import PodmanResult
from gts_agent.executors.rpmbuild import gcc_objects_reusable, rpmbuild_in_container


def test_gcc_objects_reusable_requires_stage3_cc1(tmp_path):
    assert not gcc_objects_reusable(tmp_path)
    obj = tmp_path / "rpmbuild/BUILD/gcc-14.3.0/obj-x86_64-redhat-linux"
    (obj / "gcc").mkdir(parents=True)
    (obj / "gcc" / "cc1").write_bytes(b"compiler")
    (obj / "stage_current").write_text("stage1\n")
    assert not gcc_objects_reusable(tmp_path)
    (obj / "stage_current").write_text("stage3\n")
    assert gcc_objects_reusable(tmp_path)


class _CaptureExecutor:
    def __init__(self) -> None:
        self.scripts = []

    def run(self, argv, **_kwargs):
        self.scripts.append(argv[-1] if argv else "")
        return PodmanResult(returncode=0, stdout="ok", stderr="", argv=list(argv))


def test_gcc_rpmbuild_short_circuits_when_stage3_exists(tmp_path):
    spec = tmp_path / "specs" / "gcc-toolset-14-gcc.spec"
    spec.parent.mkdir(parents=True)
    spec.write_text("Name: gcc\n", encoding="utf-8")
    obj = tmp_path / "rpmbuild/BUILD/gcc-14.3.0/obj-x86_64-redhat-linux/gcc"
    obj.mkdir(parents=True)
    (obj / "cc1").write_bytes(b"compiler")
    (obj.parent / "stage_current").write_text("stage3\n")
    executor = _CaptureExecutor()
    rpmbuild_in_container(executor, tmp_path, spec, tmp_path)
    script = executor.scripts[-1]
    assert "--short-circuit" in script
    assert "-bb" in script
    assert "debug_package %{nil}" in script


def test_gcc_rpmbuild_full_ba_without_reusable_tree(tmp_path):
    spec = tmp_path / "specs" / "gcc-toolset-14-gcc.spec"
    spec.parent.mkdir(parents=True)
    spec.write_text("Name: gcc\n", encoding="utf-8")
    executor = _CaptureExecutor()
    rpmbuild_in_container(executor, tmp_path, spec, tmp_path)
    script = executor.scripts[-1]
    assert "--short-circuit" not in script
    assert "rpmbuild -ba" in script
