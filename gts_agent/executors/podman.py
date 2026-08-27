"""Podman 隔离执行器（方案 5 E2）。

默认使用 host 网络（部分环境中 CNI DNS 无法解析发行版镜像）。
正式构建在容器内进行，不写宿主 /usr。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence


class ExecutorError(RuntimeError):
    pass


def podman_bin() -> List[str]:
    """返回可用的 podman 调用前缀（优先无 sudo）。"""
    if shutil.which("podman") is None:
        raise ExecutorError("宿主未安装 podman")
    probe = subprocess.run(
        ["podman", "info"], capture_output=True, text=True, timeout=30, check=False
    )
    if probe.returncode == 0:
        return ["podman"]
    return ["sudo", "podman"]


def image_exists(image: str) -> bool:
    try:
        argv = podman_bin() + ["image", "exists", image]
    except ExecutorError:
        return False
    result = subprocess.run(argv, capture_output=True, timeout=60, check=False)
    return result.returncode == 0


@dataclass
class PodmanResult:
    returncode: int
    stdout: str
    stderr: str
    argv: List[str] = field(default_factory=list)


class PodmanExecutor:
    def __init__(
        self,
        image: str,
        network: str = "host",
        src_root: Optional[Path] = None,
    ) -> None:
        self.image = image
        self.network = network
        self.src_root = src_root or Path(__file__).resolve().parents[2]
        self._bin = podman_bin()

    def run(
        self,
        argv: Sequence[str],
        volumes: Optional[List[str]] = None,
        env: Optional[Dict[str, str]] = None,
        workdir: str = "/job",
        timeout: int = 21600,
        user: str = "root",
    ) -> PodmanResult:
        cmd = list(self._bin) + [
            "run", "--rm",
            "--network", self.network,
            "--user", user,
            "-w", workdir,
        ]
        for volume in volumes or []:
            cmd.extend(["-v", volume])
        for key, value in (env or {}).items():
            cmd.extend(["-e", f"{key}={value}"])
        cmd.append(self.image)
        cmd.extend(list(argv))
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
        return PodmanResult(
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            argv=cmd,
        )

    def python(self, code: str, volumes: Optional[List[str]] = None,
               timeout: int = 120) -> dict:
        """在容器内执行短 Python 片段，stdout 必须是 JSON。"""
        result = self.run(
            ["python3", "-c", code],
            volumes=volumes or [f"{self.src_root}:/src:ro"],
            env={"PYTHONPATH": "/src"},
            workdir="/src",
            timeout=timeout,
        )
        if result.returncode != 0:
            raise ExecutorError(
                f"容器 Python 失败: {result.stderr[-800:] or result.stdout[-800:]}"
            )
        return json.loads(result.stdout)

    def probe_inventory(self, gcc: str = "/usr/bin/gcc") -> dict:
        code = (
            "import json,sys; sys.path.insert(0,'/src'); "
            "from gts_agent.core.probe import probe_host; "
            f"print(json.dumps(probe_host({gcc!r}).to_dict(), ensure_ascii=False))"
        )
        return self.python(code)
