"""环境探测器：只读探测宿主机/构建根，输出 Inventory。

所有探测都是确定性的命令查询（方案 9.2），不修改宿主机。
命令缺失或失败会记录为 warning，而不是静默忽略。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from typing import List, Optional, Tuple

from gts_agent.core.models.inventory import BinutilsInfo, GccInfo, Inventory


def _run(cmd: List[str], warnings: List[str]) -> Optional[str]:
    if shutil.which(cmd[0]) is None and not os.path.isabs(cmd[0]):
        warnings.append(f"命令不可用: {cmd[0]}")
        return None
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=60, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        warnings.append(f"命令执行失败: {' '.join(cmd)}: {exc}")
        return None
    if result.returncode != 0:
        warnings.append(
            f"命令退出码 {result.returncode}: {' '.join(cmd)}: {result.stderr.strip()[:200]}"
        )
        return None
    return result.stdout.strip()


def _parse_os_release() -> Tuple[str, str]:
    os_id, version_id = "", ""
    try:
        with open("/etc/os-release", "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("ID="):
                    os_id = line.split("=", 1)[1].strip('"')
                elif line.startswith("VERSION_ID="):
                    version_id = line.split("=", 1)[1].strip('"')
    except OSError:
        pass
    return os_id, version_id


def parse_gcc_major(version_output: str) -> Optional[int]:
    """从 `gcc --version` 首行解析主版本号。"""
    first_line = version_output.splitlines()[0] if version_output else ""
    match = re.search(r"\b(\d+)\.\d+\.\d+\b", first_line)
    if match:
        return int(match.group(1))
    return None


def probe_gcc(executable: str, warnings: List[str]) -> GccInfo:
    info = GccInfo(executable=executable)
    version = _run([executable, "--version"], warnings)
    if version:
        info.version = version.splitlines()[0]
        info.major = parse_gcc_major(version)
    dumpmachine = _run([executable, "-dumpmachine"], warnings)
    if dumpmachine:
        info.dumpmachine = dumpmachine
    search_dirs = _run([executable, "-print-search-dirs"], warnings)
    if search_dirs:
        info.search_dirs = search_dirs
    return info


def probe_host(gcc_executable: str = "/usr/bin/gcc") -> Inventory:
    warnings: List[str] = []
    inv = Inventory(warnings=warnings)

    inv.os_id, inv.os_version_id = _parse_os_release()
    inv.architecture = _run(["uname", "-m"], warnings) or ""
    inv.kernel = _run(["uname", "-r"], warnings) or ""

    rpm_version = _run(["rpm", "--version"], warnings)
    if rpm_version:
        inv.rpm_version = rpm_version.split()[-1]
        inv.rpm_target_platform = _run(["rpm", "-E", "%{_target_platform}"], warnings) or ""
        inv.rpm_libdir = _run(["rpm", "-E", "%{_libdir}"], warnings) or ""

    glibc = _run(["getconf", "GNU_LIBC_VERSION"], warnings)
    if glibc:
        inv.glibc_version = glibc.split()[-1]

    inv.gcc = probe_gcc(gcc_executable, warnings)

    binutils = BinutilsInfo()
    ld_version = _run(["ld", "--version"], warnings)
    if ld_version:
        binutils.ld_version = ld_version.splitlines()[0]
    as_version = _run(["as", "--version"], warnings)
    if as_version:
        binutils.as_version = as_version.splitlines()[0]
    inv.binutils = binutils

    inv.cpu_count = os.cpu_count() or 0
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        inv.memory_gib = round(pages * page_size / (1 << 30), 2)
    except (ValueError, OSError):
        pass

    return inv
