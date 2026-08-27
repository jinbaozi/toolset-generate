"""SRPM 与 Spec 解析器（方案 7 模块 3）。

- SRPM 解析依赖宿主 rpm/rpm2cpio 工具（只读查询）；
- Spec 解析为纯文本确定性解析：Source/Patch 条目、子包、宏定义、
  %files 段；不求值复杂宏，仅记录原文与可静态解析的值。
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


class SrpmError(RuntimeError):
    pass


@dataclass
class SpecPatch:
    index: int
    filename: str


@dataclass
class SpecSource:
    index: int
    value: str


@dataclass
class SpecSubpackage:
    name: str
    is_prefixed: bool  # %package -n 形式


@dataclass
class ParsedSpec:
    name: str = ""
    version: str = ""
    release: str = ""
    sources: List[SpecSource] = field(default_factory=list)
    patches: List[SpecPatch] = field(default_factory=list)
    subpackages: List[SpecSubpackage] = field(default_factory=list)
    globals: Dict[str, str] = field(default_factory=dict)
    build_requires: List[str] = field(default_factory=list)
    files_sections: List[str] = field(default_factory=list)


_GLOBAL_RE = re.compile(r"^%(?:global|define)\s+(\S+)\s+(.+)$")
_SOURCE_RE = re.compile(r"^Source(\d*):\s*(\S+)", re.IGNORECASE)
_PATCH_RE = re.compile(r"^Patch(\d*):\s*(\S+)", re.IGNORECASE)
_PACKAGE_RE = re.compile(r"^%package(?:\s+(-n)\s+(\S+)|\s+(\S+))\s*$")
_BR_RE = re.compile(r"^BuildRequires:\s*(.+)$", re.IGNORECASE)
_FILES_RE = re.compile(r"^%files(\s+.*)?$")


def parse_spec_text(text: str) -> ParsedSpec:
    spec = ParsedSpec()
    for raw_line in text.splitlines():
        line = raw_line.rstrip()

        match = _GLOBAL_RE.match(line)
        if match:
            spec.globals.setdefault(match.group(1), match.group(2))
            continue
        if line.lower().startswith("name:") and not spec.name:
            spec.name = line.split(":", 1)[1].strip()
            continue
        if line.lower().startswith("version:") and not spec.version:
            spec.version = line.split(":", 1)[1].strip()
            continue
        if line.lower().startswith("release:") and not spec.release:
            spec.release = line.split(":", 1)[1].strip()
            continue

        match = _SOURCE_RE.match(line)
        if match:
            index = int(match.group(1)) if match.group(1) else 0
            spec.sources.append(SpecSource(index=index, value=match.group(2)))
            continue
        match = _PATCH_RE.match(line)
        if match:
            index = int(match.group(1)) if match.group(1) else 0
            spec.patches.append(SpecPatch(index=index, filename=match.group(2)))
            continue
        match = _PACKAGE_RE.match(line)
        if match:
            if match.group(1):
                spec.subpackages.append(
                    SpecSubpackage(name=match.group(2), is_prefixed=True))
            else:
                spec.subpackages.append(
                    SpecSubpackage(name=match.group(3), is_prefixed=False))
            continue
        match = _BR_RE.match(line)
        if match:
            spec.build_requires.extend(
                part.strip() for part in re.split(r"[,\s]+(?=[a-zA-Z%(/])",
                                                  match.group(1)) if part.strip()
            )
            continue
        match = _FILES_RE.match(line)
        if match:
            spec.files_sections.append(line)

    spec.patches.sort(key=lambda p: p.index)
    spec.sources.sort(key=lambda s: s.index)
    return spec


def parse_spec_file(path: Path) -> ParsedSpec:
    return parse_spec_text(path.read_text(encoding="utf-8", errors="replace"))


def query_srpm(path: Path) -> Dict[str, str]:
    """rpm -qp 查询 SRPM 元数据。"""
    if shutil.which("rpm") is None:
        raise SrpmError("宿主缺少 rpm 命令，无法解析 SRPM")
    result = subprocess.run(
        ["rpm", "-qp", "--qf",
         "NAME=%{NAME}\nVERSION=%{VERSION}\nRELEASE=%{RELEASE}\nARCH=%{ARCH}\n",
         str(path)],
        capture_output=True, text=True, timeout=120, check=False,
    )
    if result.returncode != 0:
        raise SrpmError(f"rpm -qp {path} 失败: {result.stderr.strip()[:300]}")
    meta: Dict[str, str] = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            meta[key] = value
    return meta


def extract_srpm(path: Path, dest: Path) -> List[Path]:
    """rpm2cpio 展开 SRPM 内容（只用于检查，不用于直接构建发布）。"""
    for tool in ("rpm2cpio", "cpio"):
        if shutil.which(tool) is None:
            raise SrpmError(f"宿主缺少 {tool}，无法展开 SRPM")
    dest.mkdir(parents=True, exist_ok=True)
    rpm2cpio = subprocess.Popen(["rpm2cpio", str(path)], stdout=subprocess.PIPE)
    result = subprocess.run(
        ["cpio", "-idm", "--quiet"], stdin=rpm2cpio.stdout, cwd=dest,
        capture_output=True, text=True, timeout=600, check=False,
    )
    rpm2cpio.wait(timeout=600)
    if result.returncode != 0 or rpm2cpio.returncode != 0:
        raise SrpmError(f"展开 SRPM 失败: {result.stderr.strip()[:300]}")
    return sorted(p for p in dest.rglob("*") if p.is_file())
