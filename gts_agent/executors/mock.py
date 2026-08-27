"""Mock executor：生成（并在具备条件时执行）隔离构建命令。

原则：
- 正式构建必须在 Mock/chroot 中进行，绝不在宿主机直接 rpmbuild 后发布；
- 本模块默认只生成命令计划（dry-run），执行需要宿主安装 mock 并显式开启；
- 所有命令与日志路径都会记录，供状态机归档。
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


class ExecutorError(RuntimeError):
    pass


@dataclass
class MockCommand:
    description: str
    argv: List[str]

    def render(self) -> str:
        return " ".join(self.argv)


@dataclass
class MockPlan:
    mock_config: str
    commands: List[MockCommand] = field(default_factory=list)

    def render(self) -> str:
        lines = [f"# mock config: {self.mock_config}"]
        for command in self.commands:
            lines.append(f"# {command.description}")
            lines.append(command.render())
        return "\n".join(lines) + "\n"


def build_srpm_plan(
    mock_config: str,
    spec_path: Path,
    sources_dir: Path,
    result_dir: Path,
) -> MockPlan:
    plan = MockPlan(mock_config=mock_config)
    plan.commands.append(MockCommand(
        description="在干净 Mock 根中生成 SRPM",
        argv=[
            "mock", "-r", mock_config,
            "--buildsrpm",
            "--spec", str(spec_path),
            "--sources", str(sources_dir),
            "--resultdir", str(result_dir / "srpm"),
        ],
    ))
    return plan


def rebuild_rpm_plan(
    mock_config: str,
    srpm_path: Path,
    result_dir: Path,
    local_repo: Optional[Path] = None,
) -> MockPlan:
    argv = [
        "mock", "-r", mock_config,
        "--rebuild", str(srpm_path),
        "--resultdir", str(result_dir / "rpms"),
    ]
    if local_repo is not None:
        argv.extend(["--addrepo", str(local_repo)])
    plan = MockPlan(mock_config=mock_config)
    plan.commands.append(MockCommand(
        description="从 SRPM 在全新 Mock 根中重建 RPM（最终 RPM 必须由此产生）",
        argv=argv,
    ))
    return plan


def mock_available() -> bool:
    return shutil.which("mock") is not None


def execute_plan(plan: MockPlan, log_dir: Path, dry_run: bool = True) -> List[str]:
    """执行（或 dry-run 输出）Mock 计划，返回日志文件列表。"""
    log_dir.mkdir(parents=True, exist_ok=True)
    plan_file = log_dir / "mock-plan.sh"
    plan_file.write_text(plan.render(), encoding="utf-8")
    logs = [str(plan_file)]

    if dry_run:
        return logs
    if not mock_available():
        raise ExecutorError("宿主未安装 mock，无法执行隔离构建；已生成命令计划供审计")

    for index, command in enumerate(plan.commands):
        log_path = log_dir / f"mock-{index:02d}.log"
        with open(log_path, "w", encoding="utf-8") as fh:
            result = subprocess.run(
                command.argv, stdout=fh, stderr=subprocess.STDOUT, check=False
            )
        logs.append(str(log_path))
        if result.returncode != 0:
            raise ExecutorError(
                f"Mock 命令失败（退出码 {result.returncode}）：{command.render()}；"
                f"日志见 {log_path}"
            )
    return logs
