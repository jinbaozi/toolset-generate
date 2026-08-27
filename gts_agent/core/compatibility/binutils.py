"""binutils 功能探测：不能只比较版本号，必须实际测试能力（方案 9.3）。

每个探测生成一个小的 C/汇编源文件，用给定的 gcc/binutils 实际
编译并检查产物特征。探测在临时目录内进行，绝不写宿主系统路径。
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

HELLO_C = "int main(void) { return 0; }\n"


@dataclass
class ProbeResult:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class BinutilsProbeReport:
    results: List[ProbeResult] = field(default_factory=list)

    @property
    def failed(self) -> List[ProbeResult]:
        return [r for r in self.results if not r.passed]

    def to_dict(self) -> Dict[str, object]:
        return {
            "results": [
                {"name": r.name, "passed": r.passed, "detail": r.detail}
                for r in self.results
            ],
            "all_passed": not self.failed,
        }


def _run(cmd: List[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, timeout=120, check=False
    )


def _readelf(args: List[str], path: Path, cwd: Path) -> str:
    result = _run(["readelf", *args, str(path)], cwd)
    return result.stdout


def _link_probe(
    gcc: str,
    workdir: Path,
    name: str,
    extra_flags: List[str],
    expect_in_readelf: Optional[List[str]] = None,
    readelf_args: Optional[List[str]] = None,
) -> ProbeResult:
    src = workdir / f"{name}.c"
    out = workdir / f"{name}.bin"
    src.write_text(HELLO_C, encoding="utf-8")
    result = _run([gcc, str(src), "-o", str(out), *extra_flags], workdir)
    if result.returncode != 0:
        return ProbeResult(name, False, result.stderr.strip()[:400])
    if expect_in_readelf:
        output = _readelf(readelf_args or ["-dW"], out, workdir)
        missing = [token for token in expect_in_readelf if token not in output]
        if missing:
            return ProbeResult(name, False, f"readelf 输出缺少 {missing}")
    return ProbeResult(name, True)


def probe_binutils(gcc: str = "/usr/bin/gcc") -> BinutilsProbeReport:
    """针对给定 GCC driver（及其实际调用的 binutils）运行功能探测。"""
    report = BinutilsProbeReport()
    if shutil.which(gcc) is None and not Path(gcc).exists():
        report.results.append(ProbeResult("gcc-available", False, f"{gcc} 不存在"))
        return report
    if shutil.which("readelf") is None:
        report.results.append(ProbeResult("readelf-available", False, "readelf 不存在"))
        return report

    with tempfile.TemporaryDirectory(prefix="gts-binutils-probe-") as tmp:
        workdir = Path(tmp)

        # --build-id：链接后必须出现 .note.gnu.build-id
        result = _link_probe(
            gcc, workdir, "build-id", ["-Wl,--build-id"],
            expect_in_readelf=[".note.gnu.build-id"], readelf_args=["-SW"],
        )
        report.results.append(result)

        # RELRO
        report.results.append(_link_probe(
            gcc, workdir, "relro", ["-Wl,-z,relro"],
            expect_in_readelf=["GNU_RELRO"], readelf_args=["-lW"],
        ))

        # BIND_NOW
        report.results.append(_link_probe(
            gcc, workdir, "bind-now", ["-Wl,-z,now"],
            expect_in_readelf=["BIND_NOW"], readelf_args=["-dW"],
        ))

        # new dtags：--enable-new-dtags + rpath 应产生 RUNPATH 而不是 RPATH
        report.results.append(_link_probe(
            gcc, workdir, "new-dtags",
            ["-Wl,--enable-new-dtags", "-Wl,-rpath,/nonexistent-probe"],
            expect_in_readelf=["RUNPATH"], readelf_args=["-dW"],
        ))

        # PIE
        report.results.append(_link_probe(
            gcc, workdir, "pie", ["-fPIE", "-pie"],
            expect_in_readelf=["DYN"], readelf_args=["-hW"],
        ))

        # LTO：编译 + 链接全程走 -flto
        report.results.append(_link_probe(gcc, workdir, "lto", ["-flto"]))

        # DWARF 5
        report.results.append(_link_probe(gcc, workdir, "dwarf5", ["-gdwarf-5"]))

    return report
