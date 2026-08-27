"""工具链编译/链接/运行测试（方案 17.3）。"""

from __future__ import annotations

import os
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence


@dataclass
class TestCase:
    name: str
    source: str
    compiler: str          # gcc | g++
    flags: List[str] = field(default_factory=list)
    run: bool = True
    expect_exit: int = 0


@dataclass
class TestResult:
    name: str
    passed: bool
    detail: str = ""
    binary: str = ""


DEFAULT_CASES = [
    TestCase("hello-c", "hello.c", "gcc"),
    TestCase("hello-cpp", "hello.cpp", "g++", ["-std=c++17"]),
    TestCase("exceptions", "exceptions.cpp", "g++", ["-std=c++17"]),
    TestCase("rtti", "rtti.cpp", "g++", ["-std=c++17"]),
    TestCase("threads", "threads.cpp", "g++", ["-std=c++17", "-pthread"]),
    TestCase("filesystem", "filesystem.cpp", "g++", ["-std=c++17"]),
    TestCase("pie", "pie.c", "gcc", ["-fPIE", "-pie"]),
    TestCase("static-libgcc", "hello.c", "gcc", ["-static-libgcc"]),
    TestCase("static-libstdcxx", "hello.cpp", "g++", ["-static-libstdc++"]),
    TestCase("lto", "hello.c", "gcc", ["-flto"]),
    TestCase("dual-abi-1", "hello.cpp", "g++", ["-D_GLIBCXX_USE_CXX11_ABI=1"]),
    TestCase("dual-abi-0", "hello.cpp", "g++", ["-D_GLIBCXX_USE_CXX11_ABI=0"]),
]


def _run(cmd: Sequence[str], cwd: Path, env: Dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(cmd), cwd=cwd, capture_output=True, text=True, timeout=120,
        check=False, env=env,
    )


def run_toolchain_tests(
    testdir: Path,
    workdir: Path,
    env: Optional[Dict[str, str]] = None,
    gcc: str = "gcc",
    gxx: str = "g++",
    cases: Optional[List[TestCase]] = None,
) -> List[TestResult]:
    workdir.mkdir(parents=True, exist_ok=True)
    merged = dict(os.environ)
    merged.update(env or {})
    results: List[TestResult] = []
    for case in cases or DEFAULT_CASES:
        source = testdir / case.source
        if not source.exists():
            results.append(TestResult(case.name, False, f"缺少源文件 {source}"))
            continue
        compiler = gcc if case.compiler == "gcc" else gxx
        binary = workdir / case.name
        compile_cmd = [compiler, str(source), "-o", str(binary), *case.flags]
        compiled = _run(compile_cmd, workdir, merged)
        if compiled.returncode != 0:
            results.append(TestResult(
                case.name, False,
                compiled.stderr.strip()[:600] or compiled.stdout.strip()[:600],
            ))
            continue
        if not case.run:
            results.append(TestResult(case.name, True, binary=str(binary)))
            continue
        ran = _run([str(binary)], workdir, merged)
        ok = ran.returncode == case.expect_exit
        results.append(TestResult(
            case.name, ok,
            "" if ok else f"退出码 {ran.returncode}: {ran.stderr.strip()[:400]}",
            binary=str(binary),
        ))
    return results


def results_to_dict(results: List[TestResult]) -> dict:
    return {
        "passed": all(item.passed for item in results),
        "results": [asdict(item) for item in results],
    }
