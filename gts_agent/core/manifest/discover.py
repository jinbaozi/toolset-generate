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


# 文件角色 -> 子包分类规则（方案 11.2、15.1）。按顺序匹配，首个命中生效。
def classify_path(
    install_path: str,
    toolset_prefix: str,
    runtime_strategy: str,
) -> str:
    relative = install_path[len(toolset_prefix):].lstrip("/") if install_path.startswith(
        toolset_prefix
    ) else install_path
    name = os.path.basename(install_path)

    binutils_tools = {
        "as", "ld", "ld.bfd", "ar", "ranlib", "nm", "objdump", "readelf",
        "strip", "objcopy", "strings", "addr2line", "c++filt", "size",
        "elfedit", "gprof", "dwp",
    }
    cxx_drivers = {"g++", "c++"}

    if relative.startswith("bin/"):
        if name in binutils_tools:
            return "binutils"
        if name in cxx_drivers:
            return "gcc-c++"
        return "gcc"
    if relative.startswith("libexec/gcc/"):
        if name in ("cc1plus",):
            return "gcc-c++"
        return "gcc"
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
    if name.startswith("libgcc_s.so."):
        if runtime_strategy == "private-runtime":
            return "runtime-libs"
        raise ManifestError(
            "E-MANIFEST",
            f"system-nonshared 模式不应打包私有 libgcc_s DSO: {install_path}",
        )
    if relative.startswith(("lib/gcc/", "lib64/", "lib/")):
        return "gcc"
    if relative.startswith(("share/man/man1/",)):
        if name.split(".")[0] in binutils_tools:
            return "binutils"
        return "gcc"
    if relative.startswith("share/"):
        return "runtime"
    return "runtime"


def discover_staged_files(
    stage_root: Path,
    toolset_root: str,
    toolset_prefix: str,
    runtime_strategy: str,
) -> FileManifest:
    """遍历 staging 根，生成完整文件 manifest。

    stage_root: DESTDIR（如 work/JOB/stage）
    toolset_root: /opt/rh/gcc-toolset-N/root
    toolset_prefix: /opt/rh/gcc-toolset-N/root/usr
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
            entry.package = classify_path(install_path, toolset_prefix, runtime_strategy)
            manifest.entries.append(entry)
    return manifest


def write_files_lists(manifest: FileManifest, output_dir: Path) -> Dict[str, Path]:
    """为每个子包生成精确 %files 清单（每行一个绝对路径，无通配符）。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs: Dict[str, Path] = {}
    for package, entries in sorted(manifest.by_package().items()):
        if package == "UNASSIGNED":
            raise ManifestError(
                "E-MANIFEST",
                f"存在未归属文件: {[e.path for e in entries][:10]}",
            )
        target = output_dir / f"{package}.files"
        lines = [entry.path for entry in sorted(entries, key=lambda e: e.path)]
        target.write_text("\n".join(lines) + "\n", encoding="utf-8")
        outputs[package] = target
    return outputs
