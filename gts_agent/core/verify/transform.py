"""Stage 转换：按运行时策略处理 libstdc++ / libgcc_s 共享对象。

- private-runtime：保留目标 GCC 构建出的 DSO，归属 runtime-libs。
- system-nonshared：删除私有 DSO，改为指向系统库的 linker script
  （真实 nonshared 归档仍须由发行版兼容补丁产生，本步骤不伪造符号）。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List


class TransformError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code


_PRIVATE_DSO_PREFIXES = (
    "libstdc++.so.",
    "libgcc_s.so.",
)

# 与 policies/default.yaml 允许前缀一致；其余 staged 文件会导致未打包或覆盖系统路径。
_ALLOWED_STAGE_PREFIXES = (
    "/opt/rh/",
    "/usr/bin/gcc-toolset-",
    "/usr/lib/gcc-toolset/",
    "/usr/lib/rpm/macros.d/macros.gts-",
)


def _lib_dirs(stage_root: Path, toolset_prefix: str) -> List[Path]:
    prefix = stage_root / toolset_prefix.lstrip("/")
    dirs = [prefix / "lib64", prefix / "lib"]
    gcc_lib = prefix / "lib" / "gcc"
    if gcc_lib.exists():
        for root, _dirnames, _filenames in os.walk(gcc_lib):
            dirs.append(Path(root))
    return [path for path in dirs if path.exists()]


def _remove_private_dsos(directory: Path) -> List[str]:
    removed = []
    for entry in list(directory.iterdir()):
        if not entry.is_file() and not entry.is_symlink():
            continue
        if any(entry.name.startswith(prefix) for prefix in _PRIVATE_DSO_PREFIXES):
            entry.unlink()
            removed.append(str(entry))
    return removed


def _write_system_linker_scripts(libdir: Path, system_libdir: str) -> None:
    """生成指向系统 libstdc++ / libgcc_s 的链接脚本。"""
    stdcxx = libdir / "libstdc++.so"
    stdcxx.write_text(
        f"INPUT ( {system_libdir}/libstdc++.so.6 -lstdc++_nonshared "
        f"AS_NEEDED ( {system_libdir}/libstdc++.so.6 ) )\n",
        encoding="utf-8",
    )
    libgcc = libdir / "libgcc_s.so"
    libgcc.write_text(
        f"INPUT ( {system_libdir}/libgcc_s.so.1 )\n",
        encoding="utf-8",
    )


def _allowed_install_path(install_path: str) -> bool:
    return any(install_path.startswith(prefix) for prefix in _ALLOWED_STAGE_PREFIXES)


def _remove_foreign_stage_files(stage_root: Path) -> List[str]:
    """删除允许前缀之外的 staged 文件，避免 RPM 未打包文件或写入系统路径。"""
    removed: List[str] = []
    for dirpath, _dirnames, filenames in os.walk(stage_root, topdown=False):
        directory = Path(dirpath)
        for filename in filenames:
            full = directory / filename
            install_path = "/" + str(full.relative_to(stage_root))
            if _allowed_install_path(install_path):
                continue
            if full.is_symlink() or full.is_file():
                full.unlink()
                removed.append(install_path)
        if directory == stage_root:
            continue
        install_dir = "/" + str(directory.relative_to(stage_root))
        if _allowed_install_path(install_dir + "/"):
            continue
        try:
            next(directory.iterdir())
        except StopIteration:
            directory.rmdir()
        except OSError:
            continue
    return removed


def transform_stage(
    stage_root: Path,
    toolset_prefix: str,
    runtime_strategy: str,
    system_libdir: str = "/usr/lib64",
) -> List[str]:
    """按策略转换 staging 树，返回执行过的动作描述。"""
    actions: List[str] = []
    for la_path in stage_root.rglob("*.la"):
        if la_path.is_file() and not la_path.is_symlink():
            la_path.unlink()
            actions.append(f"removed libtool archive {la_path}")
    for foreign in _remove_foreign_stage_files(stage_root):
        actions.append(f"removed foreign staged file {foreign}")
    if runtime_strategy == "private-runtime":
        actions.append("private-runtime: 保留目标 libstdc++/libgcc_s DSO")
        return actions
    if runtime_strategy != "system-nonshared":
        raise TransformError("E-POLICY", f"未知 runtime_strategy: {runtime_strategy}")

    for directory in _lib_dirs(stage_root, toolset_prefix):
        removed = _remove_private_dsos(directory)
        for path in removed:
            actions.append(f"removed private DSO {path}")

    prefix = stage_root / toolset_prefix.lstrip("/")
    primary = prefix / "lib64"
    if not primary.exists():
        primary = prefix / "lib"
    primary.mkdir(parents=True, exist_ok=True)
    _write_system_linker_scripts(primary, system_libdir)
    actions.append(f"wrote system linker scripts in {primary}")
    return actions
