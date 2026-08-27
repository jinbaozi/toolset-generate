"""Inventory：宿主机/构建根探测结果的数据模型。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class GccInfo:
    executable: str
    version: str = ""
    major: Optional[int] = None
    dumpmachine: str = ""
    search_dirs: str = ""


@dataclass
class BinutilsInfo:
    ld_version: str = ""
    as_version: str = ""


@dataclass
class Inventory:
    os_id: str = ""
    os_version_id: str = ""
    architecture: str = ""
    kernel: str = ""
    rpm_version: str = ""
    rpm_target_platform: str = ""
    rpm_libdir: str = ""
    glibc_version: str = ""
    gcc: Optional[GccInfo] = None
    binutils: Optional[BinutilsInfo] = None
    cpu_count: int = 0
    memory_gib: float = 0.0
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
