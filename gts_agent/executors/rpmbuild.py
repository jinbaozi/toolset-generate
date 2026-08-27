"""在隔离容器中执行 rpmbuild，并收集 RPM/SRPM。

最终 RPM 由 SRPM 在干净容器中重建的原则：每个组件先 -bs 再 --rebuild；
MVP 为缩短迭代，seed 构建使用 -ba，随后可将 SRPM 交给干净容器 --rebuild。
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from gts_agent.executors.podman import ExecutorError, PodmanExecutor


COMPONENT_TIMEOUTS = {
    "runtime": 600,
    "binutils": 7200,
    "gcc": 28800,
}


def gcc_objects_reusable(job_dir: Path) -> bool:
    """GCC bootstrap 已完成（stage3 + cc1）时，打包失败可跳过 %prep/%build。"""
    build = job_dir / "rpmbuild" / "BUILD"
    if not build.exists():
        return False
    for gcc_src in build.glob("gcc-*"):
        if not gcc_src.is_dir():
            continue
        for objdir in gcc_src.glob("obj-*"):
            cc1 = objdir / "gcc" / "cc1"
            stage = objdir / "stage_current"
            if not cc1.is_file():
                continue
            if not stage.is_file():
                continue
            current = stage.read_text(encoding="utf-8").strip()
            if current in {"stage3", "compare"}:
                return True
    return False


def prepare_rpmbuild_tree(
    job_dir: Path,
    sources: Dict[str, Path],
    extra_files: Dict[str, Path],
) -> Path:
    topdir = job_dir / "rpmbuild"
    for sub in ("BUILD", "BUILDROOT", "RPMS", "SRPMS", "SOURCES", "SPECS"):
        (topdir / sub).mkdir(parents=True, exist_ok=True)
    for path in sources.values():
        dest = topdir / "SOURCES" / path.name
        if path.resolve() != dest.resolve():
            shutil.copy2(path, dest)
    for name, path in extra_files.items():
        shutil.copy2(path, topdir / "SOURCES" / name)
    return topdir


def collect_rpms(topdir: Path, dest: Path) -> List[Path]:
    dest.mkdir(parents=True, exist_ok=True)
    srpms = dest.parent / "srpms"
    srpms.mkdir(parents=True, exist_ok=True)
    copied: List[Path] = []
    for rpm in (topdir / "RPMS").rglob("*.rpm"):
        target = dest / rpm.name
        shutil.copy2(rpm, target)
        copied.append(target)
    for rpm in (topdir / "SRPMS").glob("*.rpm"):
        shutil.copy2(rpm, srpms / rpm.name)
    return copied


def rpmbuild_in_container(
    executor: PodmanExecutor,
    job_dir: Path,
    spec_path: Path,
    src_root: Path,
    extra_rpms: Optional[Sequence[Path]] = None,
    timeout: int = 21600,
) -> str:
    """在容器中 rpmbuild -ba spec。extra_rpms 会先 rpm -Uvh 以满足 BuildRequires。"""
    job_dir = job_dir.resolve()
    src_root = src_root.resolve()
    spec_in_job = job_dir / "specs" / spec_path.name
    if spec_path.resolve() != spec_in_job.resolve():
        spec_in_job.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(spec_path, spec_in_job)

    install_cmds = ""
    if extra_rpms:
        rpm_names = " ".join(f"/job/rpms/{path.name}" for path in extra_rpms)
        install_cmds = f"rpm -Uvh {rpm_names}\n"

    defines = [
        "--define '_topdir /job/rpmbuild'",
        "--define 'gts_agent_home /src'",
    ]
    # 最小工具集验证不需要 GCC debuginfo；find-debuginfo 会显著拉长打包。
    gcc_spec = spec_path.name.endswith("-gcc.spec")
    if gcc_spec:
        defines.append("--define 'debug_package %{nil}'")
    define_lines = " \\\n  ".join(defines)
    if gcc_spec and gcc_objects_reusable(job_dir):
        rpmbuild_mode = "--short-circuit -bb"
    else:
        rpmbuild_mode = "-ba"

    script = f"""set -euo pipefail
{install_cmds}rpmbuild {rpmbuild_mode} \\
  {define_lines} \\
  /job/specs/{spec_path.name}
"""
    volumes = [
        f"{src_root}:/src:ro",
        f"{job_dir}:/job:rw",
    ]
    result = executor.run(
        ["bash", "-lc", script],
        volumes=volumes,
        env={"PYTHONPATH": "/src", "HOME": "/tmp"},
        workdir="/job",
        timeout=timeout,
    )
    log = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0:
        raise ExecutorError(
            f"rpmbuild {spec_path.name} 失败（退出码 {result.returncode}）:\n{log[-4000:]}"
        )
    return log
