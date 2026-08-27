"""ELF 分析：基于 readelf 的确定性解析（方案 10.3、10.4）。

只读操作。输出结构与方案 10.3 的数据模型对应。
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set


@dataclass(frozen=True)
class DynamicSymbol:
    name: str
    version: Optional[str]
    defined: bool
    binding: str          # GLOBAL / WEAK / UNIQUE / LOCAL
    symbol_type: str      # FUNC / OBJECT / ...
    visibility: str       # DEFAULT / HIDDEN / PROTECTED / INTERNAL


@dataclass
class ElfInfo:
    path: str
    elf_class: str = ""
    machine: str = ""
    soname: Optional[str] = None
    interpreter: Optional[str] = None
    needed: List[str] = field(default_factory=list)
    rpath: List[str] = field(default_factory=list)
    runpath: List[str] = field(default_factory=list)
    version_definitions: List[str] = field(default_factory=list)
    version_requirements: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


def _readelf(args: List[str], path: Path) -> str:
    result = subprocess.run(
        ["readelf", *args, str(path)],
        capture_output=True, text=True, timeout=120, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"readelf {' '.join(args)} {path} 失败: {result.stderr.strip()[:300]}"
        )
    return result.stdout


def parse_elf_header(path: Path) -> ElfInfo:
    info = ElfInfo(path=str(path))
    header = _readelf(["-hW"], path)
    for line in header.splitlines():
        line = line.strip()
        if line.startswith("Class:"):
            info.elf_class = line.split(":", 1)[1].strip()
        elif line.startswith("Machine:"):
            info.machine = line.split(":", 1)[1].strip()

    program_headers = _readelf(["-lW"], path)
    match = re.search(r"Requesting program interpreter:\s*(\S+?)\]", program_headers)
    if match:
        info.interpreter = match.group(1)

    dynamic = _readelf(["-dW"], path)
    for line in dynamic.splitlines():
        needed = re.search(r"\(NEEDED\)\s+Shared library:\s+\[(.+?)\]", line)
        if needed:
            info.needed.append(needed.group(1))
            continue
        soname = re.search(r"\(SONAME\)\s+Library soname:\s+\[(.+?)\]", line)
        if soname:
            info.soname = soname.group(1)
            continue
        rpath = re.search(r"\(RPATH\)\s+Library rpath:\s+\[(.+?)\]", line)
        if rpath:
            info.rpath.extend(rpath.group(1).split(":"))
            continue
        runpath = re.search(r"\(RUNPATH\)\s+Library runpath:\s+\[(.+?)\]", line)
        if runpath:
            info.runpath.extend(runpath.group(1).split(":"))

    defs, reqs = parse_version_info(path)
    info.version_definitions = sorted(defs)
    info.version_requirements = sorted(reqs)
    return info


def parse_version_info(path: Path) -> "tuple[Set[str], Set[str]]":
    """解析 ELF 的版本定义节和版本需求节。"""
    output = _readelf(["--version-info", "-W"], path)
    definitions: Set[str] = set()
    requirements: Set[str] = set()
    section = None
    for line in output.splitlines():
        if "Version definition section" in line:
            section = "def"
            continue
        if "Version needs section" in line:
            section = "need"
            continue
        if section == "def":
            match = re.search(r"Name:\s+(\S+)", line)
            if match:
                definitions.add(match.group(1))
        elif section == "need":
            for match in re.finditer(r"Name:\s+(\S+)", line):
                requirements.add(match.group(1))
    return definitions, requirements


_SYM_LINE_RE = re.compile(
    r"^\s*\d+:\s+[0-9a-fA-F]+\s+\S+\s+(?P<type>\S+)\s+(?P<bind>\S+)\s+"
    r"(?P<vis>\S+)\s+(?P<ndx>\S+)\s+(?P<name>\S+)"
)


def parse_dynamic_symbols(path: Path) -> List[DynamicSymbol]:
    output = _readelf(["--dyn-syms", "-W"], path)
    symbols: List[DynamicSymbol] = []
    for line in output.splitlines():
        match = _SYM_LINE_RE.match(line)
        if not match:
            continue
        raw_name = match.group("name")
        if raw_name in ("UND", "Name"):
            continue
        version: Optional[str] = None
        name = raw_name
        if "@" in raw_name:
            name, _, version = raw_name.partition("@")
            version = version.lstrip("@") or None
        symbols.append(DynamicSymbol(
            name=name,
            version=version,
            defined=match.group("ndx") != "UND",
            binding=match.group("bind"),
            symbol_type=match.group("type"),
            visibility=match.group("vis"),
        ))
    return symbols


def scan_glibc_requirements(paths: List[Path]) -> Set[str]:
    """收集一组 ELF 的 GLIBC_* 版本需求并集（方案 9.4 的输入）。"""
    required: Set[str] = set()
    for path in paths:
        _, reqs = parse_version_info(path)
        required.update(node for node in reqs if node.startswith("GLIBC_"))
    return required
