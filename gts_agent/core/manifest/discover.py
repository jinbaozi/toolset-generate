"""staged 文件发现与精确 %files 生成（方案 11.3、11.5）。

流程：
1. 用 staged GCC 的 -print-* 查询发现关键文件；
2. 遍历 staging 根，为每个文件计算哈希、realpath、类型；
3. 校验 realpath 不逃逸 staging 根（禁止复制宿主文件）；
4. 按运行时策略和文件角色分类到子包；
5. 输出 manifest YAML 与逐包 .files 清单（禁止宽泛通配符）。
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import yaml


class ManifestError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code


@dataclass
class ManifestEntry:
    path: str                 # 安装后的绝对路径（不含 staging 根）
    file_type: str            # regular | symlink | directory
    mode: str = "0644"
    sha256: Optional[str] = None
    symlink_target: Optional[str] = None
    package: str = ""         # 归属子包逻辑名
    reason: str = ""


@dataclass
class FileManifest:
    schema_version: int = 1
    toolset_root: str = ""
    entries: List[ManifestEntry] = field(default_factory=list)

    def by_package(self) -> Dict[str, List[ManifestEntry]]:
        result: Dict[str, List[ManifestEntry]] = {}
        for entry in self.entries:
            result.setdefault(entry.package or "UNASSIGNED", []).append(entry)
        return result

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "schema_version": self.schema_version,
            "toolset_root": self.toolset_root,
            "files": [asdict(entry) for entry in self.entries],
        }
        path.write_text(
            yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )


def gcc_print_file_name(gcc: str, filename: str) -> Optional[str]:
    """gcc -print-file-name=X；返回原始字符串表示未找到，此时返回 None。"""
    result = subprocess.run(
        [gcc, f"-print-file-name={filename}"],
        capture_output=True, text=True, timeout=60, check=False,
    )
    if result.returncode != 0:
        return None
    output = result.stdout.strip()
    if output == filename or not output:
        return None
    return output


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


_BINUTILS_TOOLS = {
    "as", "ld", "ld.bfd", "ar", "ranlib", "nm", "objdump", "readelf",
    "strip", "objcopy", "strings", "addr2line", "c++filt", "size",
    "elfedit", "gprof", "dwp", "coffdump", "srconv", "sysdump",
}
_CXX_DRIVERS = {"g++", "c++"}

# 私有运行时 DSO 的 SONAME 前缀（private-runtime 模式归入 runtime-libs）
_RUNTIME_DSO_PREFIXES = (
    "libstdc++.so.", "libgcc_s.so.", "libatomic.so.", "libgomp.so.",
    "libquadmath.so.", "libitm.so.", "libssp.so.",
)
# 未带 SONAME 版本的链接名（libstdc++.so 仍归 devel）
_UNVERSIONED_RUNTIME_LINKER_NAMES = {
    "libgcc_s.so", "libatomic.so", "libgomp.so", "libquadmath.so",
    "libitm.so", "libssp.so",
}

GCC_FILE_PACKAGES = ("gcc", "gcc-c++", "libstdc++-devel")
GCC_PRIVATE_RUNTIME_PACKAGES = GCC_FILE_PACKAGES + ("runtime-libs",)


def _strip_triple_prefix(name: str) -> str:
    """去掉 x86_64-redhat-linux- 之类的 triple 前缀，返回基础工具名。"""
    parts = name.split("-")
    if len(parts) >= 4 and "linux" in parts:
        idx = max(i for i, part in enumerate(parts) if "linux" in part)
        rest = "-".join(parts[idx + 1:])
        return rest or name
    return name


# 文件角色 -> 子包分类规则（方案 11.2、15.1）。按顺序匹配，首个命中生效。
def classify_path(
    install_path: str,
    toolset_prefix: str,
    runtime_strategy: str,
    component: str = "gcc",
) -> str:
    """component:
    - "gcc"：GCC 构建根，拆分为 gcc/gcc-c++/libstdc++-devel(/runtime-libs)
    - "binutils"/"runtime"：单包构建根，全部文件归属该包
    """
    if component != "gcc":
        return component

    relative = install_path[len(toolset_prefix):].lstrip("/") if install_path.startswith(
        toolset_prefix
    ) else install_path
    name = os.path.basename(install_path)
    base_name = _strip_triple_prefix(name)

    top = relative.split("/", 1)[0] if "/" in relative else relative

    if relative.startswith("include/c++/"):
        return "libstdc++-devel"
    if "libstdc++_nonshared" in name:
        if runtime_strategy != "system-nonshared":
            raise ManifestError(
                "E-NONSHARED-MISMATCH",
                f"private-runtime 模式不应产生 nonshared 归档: {install_path}",
            )
        return "libstdc++-devel"
    if name.startswith(("libstdc++", "libsupc++")):
        if name.endswith((".a", ".la")) or name == "libstdc++.so":
            return "libstdc++-devel"
        if ".so." in name:
            if runtime_strategy == "private-runtime":
                return "runtime-libs"
            raise ManifestError(
                "E-MANIFEST",
                f"system-nonshared 模式不应打包私有 libstdc++ DSO: {install_path}",
            )
    if any(name.startswith(prefix) for prefix in _RUNTIME_DSO_PREFIXES) or \
            name in _UNVERSIONED_RUNTIME_LINKER_NAMES:
        if runtime_strategy == "private-runtime":
            return "runtime-libs"
        raise ManifestError(
            "E-MANIFEST",
            f"system-nonshared 模式不应打包私有运行时 DSO: {install_path}",
        )
    if relative.startswith("bin/"):
        if base_name in _BINUTILS_TOOLS or name in _BINUTILS_TOOLS:
            return "binutils"
        if name in _CXX_DRIVERS or base_name in _CXX_DRIVERS or \
                name.endswith(("-g++", "-c++")):
            return "gcc-c++"
        return "gcc"
    # binutils tooldir 副本：<prefix>/<triple>/bin、<prefix>/<triple>/lib/ldscripts
    if _is_tooldir_binutils(relative, top):
        return "binutils"
    if relative.startswith("libexec/gcc/"):
        if name in ("cc1plus",):
            return "gcc-c++"
        return "gcc"
    # libstdc++ pretty printers / python 模块
    if relative.startswith("share/gcc-") or "/python/" in relative:
        return "libstdc++-devel"
    if relative.startswith(("lib/gcc/", "lib64/", "lib/", "libexec/")):
        return "gcc"
    if "linux" in top and "/lib/" in f"/{relative}/":
        return "gcc"
    if relative.startswith("share/man/man1/"):
        stem = name.split(".")[0]
        if stem in _BINUTILS_TOOLS or _strip_triple_prefix(stem) in _BINUTILS_TOOLS:
            return "binutils"
        if stem in _CXX_DRIVERS or stem.endswith(("g++", "c++")):
            return "gcc-c++"
        return "gcc"
    if relative.startswith(("include/", "share/")):
        return "gcc"
    return "gcc"


def _is_tooldir_binutils(relative: str, top: str) -> bool:
    """$prefix/$triple/{bin,lib/ldscripts} 是 binutils 已打包内容。"""
    if top in ("lib", "lib64", "libexec", "bin", "include", "share") or "linux" not in top:
        return False
    parts = relative.split("/")
    if len(parts) >= 2 and parts[1] == "bin":
        return True
    return "ldscripts" in parts


def discover_staged_files(
    stage_root: Path,
    toolset_root: str,
    toolset_prefix: str,
    runtime_strategy: str,
    component: str = "gcc",
) -> FileManifest:
    """遍历 staging 根，生成完整文件 manifest（含目录归属）。

    stage_root: DESTDIR（如 work/JOB/stage）
    toolset_root: /opt/rh/gcc-toolset-N/root
    toolset_prefix: /opt/rh/gcc-toolset-N/root/usr
    component: gcc（拆分）| binutils | runtime（单包）
    """
    stage_root = stage_root.resolve()
    staged_toolset = stage_root / toolset_root.lstrip("/")
    if not staged_toolset.exists():
        raise ManifestError(
            "E-MANIFEST",
            f"staging 中不存在 Toolset 根 {staged_toolset}",
        )

    manifest = FileManifest(toolset_root=toolset_root)
    for dirpath, _dirnames, filenames in os.walk(staged_toolset):
        for filename in sorted(filenames):
            full = Path(dirpath) / filename
            install_path = "/" + str(full.relative_to(stage_root))
            if full.is_symlink():
                target = os.readlink(full)
                # 按安装时语义校验：链接目标必须仍位于 Toolset 根内，
                # 禁止指向系统路径（如 /usr/bin/ld）或逃逸 Toolset 前缀。
                if os.path.isabs(target):
                    resolved_install = os.path.normpath(target)
                else:
                    install_dir = os.path.dirname(install_path)
                    resolved_install = os.path.normpath(
                        os.path.join(install_dir, target)
                    )
                if not resolved_install.startswith(toolset_root.rstrip("/") + "/"):
                    raise ManifestError(
                        "E-ISOLATION",
                        f"符号链接 {install_path} 指向 Toolset 根之外: {target}",
                    )
                entry = ManifestEntry(
                    path=install_path,
                    file_type="symlink",
                    symlink_target=target,
                    mode="0777",
                )
            else:
                entry = ManifestEntry(
                    path=install_path,
                    file_type="regular",
                    mode=format(full.stat().st_mode & 0o7777, "04o"),
                    sha256=_sha256(full),
                )
            entry.package = classify_path(
                install_path, toolset_prefix, runtime_strategy, component
            )
            manifest.entries.append(entry)

    _scan_extra_runtime_paths(manifest, stage_root, toolset_prefix, runtime_strategy, component)
    if component == "gcc":
        _prune_binutils_owned(manifest, stage_root, staged_toolset)
        _require_private_runtime_dsos(manifest, runtime_strategy)
    _assign_directories(manifest, stage_root, staged_toolset, component)
    return manifest


def _prune_binutils_owned(
    manifest: FileManifest,
    stage_root: Path,
    staged_toolset: Path,
) -> None:
    """GCC 构建根里的 binutils 工具已由 binutils RPM 拥有，删除以免未打包/冲突。"""
    kept: List[ManifestEntry] = []
    for entry in manifest.entries:
        if entry.package != "binutils":
            kept.append(entry)
            continue
        full = stage_root / entry.path.lstrip("/")
        if full.is_symlink() or full.is_file():
            full.unlink(missing_ok=True)
    manifest.entries = kept
    _remove_empty_directories(staged_toolset)


def _remove_empty_directories(root: Path) -> None:
    if not root.exists():
        return
    for dirpath, _dirnames, _filenames in os.walk(root, topdown=False):
        directory = Path(dirpath)
        if directory == root:
            continue
        try:
            next(directory.iterdir())
        except StopIteration:
            directory.rmdir()
        except OSError:
            continue


def _require_private_runtime_dsos(manifest: FileManifest, runtime_strategy: str) -> None:
    if runtime_strategy != "private-runtime":
        return
    dsos = [
        entry for entry in manifest.entries
        if entry.package == "runtime-libs" and entry.file_type != "directory"
    ]
    if not dsos:
        raise ManifestError(
            "E-MANIFEST",
            "private-runtime 未发现 libstdc++/libgcc_s 等运行时 DSO",
        )


def _scan_extra_runtime_paths(
    manifest: FileManifest,
    stage_root: Path,
    toolset_prefix: str,
    runtime_strategy: str,
    component: str,
) -> None:
    """扫描 Toolset 根之外、策略允许的 wrapper/宏路径。"""
    extra = [
        stage_root / "usr" / "bin",
        stage_root / "usr" / "lib" / "gcc-toolset",
        stage_root / "usr" / "lib" / "rpm" / "macros.d",
    ]
    seen = {entry.path for entry in manifest.entries}
    for root in extra:
        if not root.exists():
            continue
        for full in sorted(root.rglob("*")):
            if not full.is_file() and not full.is_symlink():
                continue
            install_path = "/" + str(full.relative_to(stage_root))
            if install_path in seen:
                continue
            if full.is_symlink():
                entry = ManifestEntry(
                    path=install_path,
                    file_type="symlink",
                    symlink_target=os.readlink(full),
                    mode="0777",
                )
            else:
                entry = ManifestEntry(
                    path=install_path,
                    file_type="regular",
                    mode=format(full.stat().st_mode & 0o7777, "04o"),
                    sha256=_sha256(full),
                )
            entry.package = classify_path(
                install_path, toolset_prefix, runtime_strategy, component
            )
            manifest.entries.append(entry)
            seen.add(install_path)


def _assign_directories(
    manifest: FileManifest,
    stage_root: Path,
    staged_toolset: Path,
    component: str,
) -> None:
    """为 Toolset 根下的每个目录生成 %dir 条目。

    目录归属规则：仅被一个包使用 -> 该包；被多个包共享 -> 组件默认包
    （RPM 允许多包共同拥有相同属性的目录）。
    """
    dir_owners: Dict[str, set] = {}
    for entry in manifest.entries:
        parent = os.path.dirname(entry.path)
        toolset_root_str = "/" + str(staged_toolset.relative_to(stage_root))
        while parent.startswith(toolset_root_str) and parent != "/":
            dir_owners.setdefault(parent, set()).add(entry.package)
            if parent == toolset_root_str:
                break
            parent = os.path.dirname(parent)

    default_package = component if component != "gcc" else "gcc"
    for dir_path in sorted(dir_owners):
        owners = dir_owners[dir_path]
        package = next(iter(owners)) if len(owners) == 1 else default_package
        full = stage_root / dir_path.lstrip("/")
        mode = format(full.stat().st_mode & 0o7777, "04o") if full.exists() else "0755"
        manifest.entries.append(ManifestEntry(
            path=dir_path,
            file_type="directory",
            mode=mode,
            package=package,
        ))


def write_files_lists(
    manifest: FileManifest,
    output_dir: Path,
    required_packages: Optional[List[str]] = None,
) -> Dict[str, Path]:
    """为每个子包生成精确 %files 清单（每行一个绝对路径，无通配符）。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs: Dict[str, Path] = {}
    grouped = manifest.by_package()
    packages = set(grouped)
    if required_packages:
        packages.update(required_packages)
    for package in sorted(packages):
        if package == "UNASSIGNED":
            entries = grouped.get(package, [])
            raise ManifestError(
                "E-MANIFEST",
                f"存在未归属文件: {[e.path for e in entries][:10]}",
            )
        entries = grouped.get(package, [])
        target = output_dir / f"{package}.files"
        lines = []
        for entry in sorted(entries, key=lambda e: (e.file_type != "directory", e.path)):
            if entry.file_type == "directory":
                lines.append(f"%dir {entry.path}")
            else:
                lines.append(entry.path)
        text = "\n".join(lines)
        if text:
            text += "\n"
        target.write_text(text, encoding="utf-8")
        outputs[package] = target
    return outputs
