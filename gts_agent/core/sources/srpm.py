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
from typing import Dict, List, Optional, Sequence


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
_MACRO_RE = re.compile(r"%\{([A-Za-z0-9_]+)\}")


def expand_simple_macros(value: str, macros: Dict[str, str]) -> str:
    """只展开 %{name} 形式的静态宏，不求值条件/shell/复杂表达式。"""
    current = value
    for _ in range(12):
        nxt = _MACRO_RE.sub(lambda match: macros.get(match.group(1), match.group(0)), current)
        if nxt == current:
            return current
        current = nxt
    return current


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


def spec_to_dict(spec: ParsedSpec) -> Dict[str, object]:
    macros = dict(spec.globals)
    if spec.version:
        macros.setdefault("version", spec.version)
    if spec.release:
        macros.setdefault("release", spec.release)
    if spec.name:
        macros.setdefault("name", spec.name)
    return {
        "name": spec.name,
        "name_expanded": expand_simple_macros(spec.name, macros),
        "version": spec.version,
        "version_expanded": expand_simple_macros(spec.version, macros),
        "release": spec.release,
        "sources": [
            {
                "index": item.index,
                "value": item.value,
                "value_expanded": expand_simple_macros(item.value, macros),
            }
            for item in spec.sources
        ],
        "patches": [
            {
                "index": item.index,
                "filename": item.filename,
                "filename_expanded": expand_simple_macros(item.filename, macros),
            }
            for item in spec.patches
        ],
        "subpackages": [
            {"name": item.name, "is_prefixed": item.is_prefixed}
            for item in spec.subpackages
        ],
        "globals": spec.globals,
        "build_requires": spec.build_requires,
        "files_sections": spec.files_sections,
    }


def index_spec_dir(specs_dir: Path) -> List[Dict[str, object]]:
    """解析目录中全部 Spec，供 GenerateRPM / 报告使用。"""
    result: List[Dict[str, object]] = []
    if not specs_dir.is_dir():
        return result
    for path in sorted(specs_dir.glob("*.spec")):
        item = spec_to_dict(parse_spec_file(path))
        item["path"] = path.name
        result.append(item)
    return result


def _require_tool(name: str) -> None:
    if shutil.which(name) is None:
        raise SrpmError(f"宿主缺少 {name} 命令，无法解析 SRPM")


def query_srpm(path: Path) -> Dict[str, str]:
    """rpm -qp 查询 SRPM 元数据。"""
    _require_tool("rpm")
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


def list_srpm_payload(path: Path) -> List[str]:
    """列出 SRPM 载荷文件（rpm -qpl）。"""
    _require_tool("rpm")
    result = subprocess.run(
        ["rpm", "-qpl", str(path)],
        capture_output=True, text=True, timeout=120, check=False,
    )
    if result.returncode != 0:
        raise SrpmError(f"rpm -qpl {path} 失败: {result.stderr.strip()[:300]}")
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _payload_matches(name: str, suffixes: Sequence[str]) -> bool:
    lower = name.lower()
    return any(lower.endswith(suffix.lower()) for suffix in suffixes)


def extract_srpm(
    path: Path,
    dest: Path,
    suffixes: Optional[Sequence[str]] = None,
) -> List[Path]:
    """rpm2cpio 展开 SRPM 内容（只用于检查，不用于直接构建发布）。

    suffixes 非空时只展开 Spec/补丁等元数据文件，避免解开完整 gcc tarball。
    """
    for tool in ("rpm2cpio", "cpio"):
        _require_tool(tool)
    dest.mkdir(parents=True, exist_ok=True)
    names: Optional[List[str]] = None
    if suffixes:
        names = [
            item for item in list_srpm_payload(path)
            if _payload_matches(item, suffixes)
        ]
        if not names:
            return []
    rpm2cpio = subprocess.Popen(["rpm2cpio", str(path)], stdout=subprocess.PIPE)
    try:
        argv = ["cpio", "-idm", "--quiet"]
        if names:
            argv.extend(names)
        result = subprocess.run(
            argv, stdin=rpm2cpio.stdout, cwd=dest,
            capture_output=True, text=True, timeout=600, check=False,
        )
    finally:
        if rpm2cpio.stdout is not None:
            rpm2cpio.stdout.close()
        rpm2cpio.wait(timeout=600)
    if result.returncode != 0 or rpm2cpio.returncode not in (0, None):
        raise SrpmError(f"展开 SRPM 失败: {result.stderr.strip()[:300]}")
    return sorted(p for p in dest.rglob("*") if p.is_file())


def inspect_srpm(
    path: Path,
    dest: Path,
    suffixes: Sequence[str] = (".spec", ".patch", ".diff"),
) -> Dict[str, object]:
    """查询 SRPM 元数据、展开 Spec/补丁并解析 Spec。"""
    meta = query_srpm(path)
    payload = list_srpm_payload(path)
    extracted = extract_srpm(path, dest, suffixes=suffixes)
    specs = [item for item in extracted if item.suffix == ".spec"]
    parsed = []
    for spec_path in specs:
        item = spec_to_dict(parse_spec_file(spec_path))
        item["path"] = spec_path.name
        parsed.append(item)
    return {
        "srpm": path.name,
        "meta": meta,
        "nevr": "{0}-{1}-{2}".format(
            meta.get("NAME", ""), meta.get("VERSION", ""), meta.get("RELEASE", ""),
        ),
        "payload": payload,
        "extracted": [str(item) for item in extracted],
        "specs": parsed,
        "parsed": bool(parsed),
    }


def inspect_srpm_dir(
    srpms_dir: Path,
    extract_root: Path,
) -> Dict[str, object]:
    reports = []
    if srpms_dir.is_dir():
        for srpm in sorted(srpms_dir.glob("*.src.rpm")):
            reports.append(
                inspect_srpm(srpm, extract_root / srpm.name.replace(".src.rpm", ""))
            )
    return {
        "passed": all(item.get("parsed") for item in reports) if reports else True,
        "srpms": reports,
    }
