"""Approval 之后的可恢复流水线：Patch → Build → Stage → RPM → 测试 → 报告。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Dict, List, Tuple

import yaml

from gts_agent.agent.approvals import require_approval
from gts_agent.agent.policy_engine import load_policy
from gts_agent.agent.state_machine import State
from gts_agent.core.models.source_lock import SourceLock
from gts_agent.core.sources.patches import apply_patch_manifest
from gts_agent.core.verify.abi import analyze_binary
from gts_agent.core.verify.isolation import compare_snapshots, save_snapshot, take_snapshot
from gts_agent.core.verify.report import build_provenance, build_sbom, write_json
from gts_agent.core.verify.rpm import inspect_rpm_dir
from gts_agent.core.verify.toolchain import results_to_dict, run_toolchain_tests
from gts_agent.executors.podman import ExecutorError, PodmanExecutor, image_exists
from gts_agent.executors.rpmbuild import (
    COMPONENT_TIMEOUTS,
    collect_rpms,
    prepare_rpmbuild_tree,
    rpmbuild_in_container,
)

_PKG_ROOT = Path(__file__).resolve().parent.parent
_TEMPLATE_ROOT = _PKG_ROOT / "templates"


class Pipeline:
    def __init__(self, orch: Any) -> None:
        self.orch = orch
        self.src_root = _PKG_ROOT.parent
        self.executor: PodmanExecutor | None = None
        if orch.config.build_executor == "podman":
            if not image_exists(orch.config.build_image):
                raise ExecutorError(
                    f"Podman 镜像 {orch.config.build_image} 不存在；"
                    "请先构建 containers/rhel9-builder"
                )
            self.executor = PodmanExecutor(
                orch.config.build_image, src_root=self.src_root
            )

    def mark_approval(self) -> None:
        plan_sha = self.orch.plan_sha256()

        def runner(_input: Any) -> Tuple[Dict[str, Any], Dict[str, Any]]:
            record = require_approval(self.orch.job_dir, plan_sha, scope="build-plan")
            extra = {}
            if (
                self.orch.config.toolset.runtime_strategy == "private-runtime"
                and self.orch.config.policy.require_private_runtime_approval
            ):
                extra_record = require_approval(
                    self.orch.job_dir, plan_sha, scope="private-runtime"
                )
                extra["private_runtime"] = extra_record.approver
            return {
                "plan_sha256": plan_sha,
                "approver": record.approver,
                **extra,
            }, {}

        self.orch.machine.run_state(
            State.APPROVAL, {"plan_sha256": plan_sha}, runner
        )

    def patch_transform(self) -> None:
        def runner(_input: Any) -> Tuple[Dict[str, Any], Dict[str, Any]]:
            manifest = self.orch.job_dir / "sources" / "patch-manifest.yaml"
            if not manifest.exists():
                shutil.copy2(
                    _TEMPLATE_ROOT / "patch-manifest.empty.yaml", manifest
                )
            report = apply_patch_manifest(
                manifest, self.orch.job_dir / "sources"
            )
            out = self.orch.job_dir / "logs" / "patch-report.json"
            report.save(out)
            return {"applied": len(report.applications)}, {"log_artifacts": [str(out)]}

        self.orch.machine.run_state(
            State.PATCH_TRANSFORM,
            {"manifest": "patch-manifest.yaml"},
            runner,
        )

    def build(self) -> List[Path]:
        if self.executor is None:
            raise ExecutorError("当前 executor 不是 podman，无法执行隔离 rpmbuild")

        def runner(_input: Any) -> Tuple[Dict[str, Any], Dict[str, Any]]:
            job_dir = self.orch.job_dir
            logs_dir = job_dir / "logs"
            logs_dir.mkdir(parents=True, exist_ok=True)
            source_files = {
                path.name: path
                for path in (job_dir / "sources").glob("*")
                if path.is_file()
            }
            extra = {
                "enable.in": _TEMPLATE_ROOT / "activation" / "enable.in",
                "env-wrapper.in": _TEMPLATE_ROOT / "activation" / "env-wrapper.in",
                "source.lock.json": job_dir / "source.lock.json",
                "patch-manifest.yaml": job_dir / "sources" / "patch-manifest.yaml",
            }
            prepare_rpmbuild_tree(job_dir, source_files, extra)

            built: List[str] = []
            extra_rpms: List[Path] = []
            rpm_dest = job_dir / "rpms"
            order = ["runtime", "binutils", "gcc"]
            for component in order:
                spec = job_dir / "specs" / (
                    f"gcc-toolset-{self.orch.config.toolset_id}-{component}.spec"
                )
                log = rpmbuild_in_container(
                    self.executor,
                    job_dir,
                    spec,
                    self.src_root,
                    extra_rpms=extra_rpms,
                    timeout=COMPONENT_TIMEOUTS.get(component, 21600),
                )
                (logs_dir / f"rpmbuild-{component}.log").write_text(log, encoding="utf-8")
                copied = collect_rpms(job_dir / "rpmbuild", rpm_dest)
                extra_rpms = [
                    path for path in copied
                    if not path.name.endswith(".src.rpm")
                ]
                built.append(component)
            return {"components": built, "rpms": [p.name for p in extra_rpms]}, {
                "log_artifacts": [str(logs_dir / f"rpmbuild-{c}.log") for c in built]
            }

        self.orch.machine.run_state(
            State.BUILD, {"spec_set": self.orch.plan_sha256()}, runner
        )
        return list((self.orch.job_dir / "rpms").glob("*.rpm"))

    def stage_install(self) -> None:
        def runner(_input: Any) -> Tuple[Dict[str, Any], Dict[str, Any]]:
            reports = list((self.orch.job_dir / "rpmbuild" / "BUILD").rglob("stage-verify.json"))
            if not reports:
                # %check 把报告写在 %{_builddir}/stage-verify.json = BUILD/stage-verify.json
                fallback = self.orch.job_dir / "rpmbuild" / "BUILD" / "stage-verify.json"
                reports = [fallback] if fallback.exists() else []
            if not reports:
                raise RuntimeError("未找到 stage-verify.json；rpmbuild %check 可能未运行")
            data = json.loads(reports[-1].read_text(encoding="utf-8"))
            if not data.get("passed", False):
                raise RuntimeError(f"Stage 验证失败: {data}")
            return data, {"log_artifacts": [str(path) for path in reports]}

        self.orch.machine.run_state(State.STAGE_INSTALL, {"build": "ok"}, runner)

    def generate_rpm(self) -> None:
        def runner(_input: Any) -> Tuple[Dict[str, Any], Dict[str, Any]]:
            rpms = sorted((self.orch.job_dir / "rpms").glob("*.rpm"))
            if not rpms:
                raise RuntimeError("rpms/ 目录为空")
            reports_dir = self.orch.job_dir / "reports"
            reports_dir.mkdir(parents=True, exist_ok=True)
            if self.executor is not None:
                script = """
set -euo pipefail
createrepo_c /job/rpms
python3 - <<'PY'
import json, sys
from pathlib import Path
sys.path.insert(0, "/src")
from gts_agent.agent.policy_engine import load_policy
from gts_agent.core.verify.rpm import inspect_rpm_dir
inspection = inspect_rpm_dir(Path("/job/rpms"), load_policy("default"))
Path("/job/reports/rpm-inspect.json").write_text(
    json.dumps(inspection, indent=2, ensure_ascii=False) + "\\n"
)
if not inspection["passed"]:
    raise SystemExit("RPM policy check failed")
print("rpm-inspect-ok")
PY
"""
                result = self.executor.run(
                    ["bash", "-lc", script],
                    volumes=[
                        f"{self.src_root}:/src:ro",
                        f"{self.orch.job_dir}:/job:rw",
                    ],
                    env={"PYTHONPATH": "/src"},
                    workdir="/job",
                    timeout=180,
                )
                if result.returncode != 0:
                    raise ExecutorError(result.stderr[-800:] + result.stdout[-800:])
                inspection = json.loads(
                    (reports_dir / "rpm-inspect.json").read_text(encoding="utf-8")
                )
            else:
                policy = load_policy(self.orch.policy.policy_id)
                inspection = inspect_rpm_dir(self.orch.job_dir / "rpms", policy)
                (reports_dir / "rpm-inspect.json").write_text(
                    json.dumps(inspection, indent=2, ensure_ascii=False) + "\n"
                )
                if not inspection["passed"]:
                    raise RuntimeError("RPM 策略检查失败")
            return inspection, {"log_artifacts": [str(reports_dir / "rpm-inspect.json")]}

        self.orch.machine.run_state(State.GENERATE_RPM, {"rpms": "collected"}, runner)

    def install_and_test(self) -> None:
        if self.executor is None:
            raise ExecutorError("需要 podman 执行器才能做安装/隔离测试")
        job_dir = self.orch.job_dir
        tests_src = self.src_root / "tests"
        glibc = self.orch.config.platform.glibc_baseline or "2.34"
        toolset_id = self.orch.config.toolset_id
        strategy = self.orch.config.toolset.runtime_strategy
        prefix = self.orch.config.toolset.prefix

        def install_runner(_input: Any) -> Tuple[Dict[str, Any], Dict[str, Any]]:
            script = f"""
set -euo pipefail
python3 - <<'PY'
import json, sys
sys.path.insert(0, "/src")
from gts_agent.core.verify.isolation import take_snapshot, save_snapshot
save_snapshot(take_snapshot(), "/job/reports/snapshot-before.json")
print("snapshot-before")
PY
rpm -Uvh /job/rpms/*.rpm
python3 - <<'PY'
import json, sys
sys.path.insert(0, "/src")
from gts_agent.core.verify.isolation import (
    compare_snapshots, save_snapshot, take_snapshot,
)
import json as js
from pathlib import Path
before = js.loads(Path("/job/reports/snapshot-before.json").read_text())
after = take_snapshot()
save_snapshot(after, "/job/reports/snapshot-after.json")
changes = compare_snapshots(before, after)
Path("/job/reports/isolation.json").write_text(js.dumps({{
    "changes": changes, "passed": len(changes) == 0
}}, indent=2) + "\\n")
if changes:
    raise SystemExit(f"系统路径被修改: {{changes}}")
print("isolation-ok")
PY
test -x /usr/bin/gcc-toolset-{toolset_id}-env
test -f /opt/rh/gcc-toolset-{toolset_id}/enable
"""
            result = self.executor.run(
                ["bash", "-lc", script],
                volumes=[
                    f"{self.src_root}:/src:ro",
                    f"{job_dir}:/job:rw",
                    f"{tests_src}:/tests:ro",
                ],
                env={"PYTHONPATH": "/src"},
                workdir="/job",
                timeout=600,
            )
            log = result.stdout + result.stderr
            (job_dir / "logs" / "install-test.log").write_text(log, encoding="utf-8")
            if result.returncode != 0:
                raise ExecutorError(f"安装/隔离测试失败:\n{log[-3000:]}")
            isolation = json.loads((job_dir / "reports" / "isolation.json").read_text())
            return isolation, {"log_artifacts": [str(job_dir / "logs" / "install-test.log")]}

        self.orch.machine.run_state(State.INSTALL_TEST, {"rpms": "ready"}, install_runner)

        def compile_runner(_input: Any) -> Tuple[Dict[str, Any], Dict[str, Any]]:
            script = f"""
set -euo pipefail
source /opt/rh/gcc-toolset-{toolset_id}/enable
python3 - <<'PY'
import json, os, sys
from pathlib import Path
sys.path.insert(0, "/src")
from gts_agent.core.verify.toolchain import results_to_dict, run_toolchain_tests
results = run_toolchain_tests(
    testdir=Path("/tests/gcc"),
    workdir=Path("/job/tests/gcc-out"),
    env=dict(os.environ),
)
Path("/job/reports/toolchain.json").write_text(
    json.dumps(results_to_dict(results), indent=2, ensure_ascii=False) + "\\n"
)
if not all(item.passed for item in results):
    raise SystemExit("toolchain tests failed")
print("toolchain-ok")
PY
"""
            result = self.executor.run(
                ["bash", "-lc", script],
                volumes=[
                    f"{self.src_root}:/src:ro",
                    f"{job_dir}:/job:rw",
                    f"{tests_src}:/tests:ro",
                ],
                env={"PYTHONPATH": "/src"},
                timeout=600,
            )
            log = result.stdout + result.stderr
            (job_dir / "logs" / "compile-test.log").write_text(log, encoding="utf-8")
            if result.returncode != 0:
                raise ExecutorError(f"编译/链接测试失败:\n{log[-3000:]}")
            data = json.loads((job_dir / "reports" / "toolchain.json").read_text())
            return data, {"log_artifacts": [str(job_dir / "logs" / "compile-test.log")]}

        self.orch.machine.run_state(
            State.COMPILE_LINK_TEST, {"install": "ok"}, compile_runner
        )

        def abi_runner(_input: Any) -> Tuple[Dict[str, Any], Dict[str, Any]]:
            reports = []
            out_dir = job_dir / "tests" / "gcc-out"
            for binary in sorted(out_dir.glob("*")):
                if not binary.is_file() or binary.suffix:
                    continue
                try:
                    reports.append(analyze_binary(
                        binary, glibc, strategy, f"{prefix}/lib64"
                    ))
                except Exception as exc:  # noqa: BLE001
                    reports.append({
                        "path": str(binary), "passed": False, "issues": [str(exc)]
                    })
            payload = {
                "passed": all(item.get("passed") for item in reports) if reports else False,
                "binaries": reports,
            }
            path = job_dir / "reports" / "abi.json"
            path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
            if not payload["passed"]:
                raise RuntimeError(f"ABI 检查失败，见 {path}")
            return payload, {"log_artifacts": [str(path)]}

        self.orch.machine.run_state(State.ABI_SYMBOL_TEST, {"compile": "ok"}, abi_runner)

        def isolation_runner(_input: Any) -> Tuple[Dict[str, Any], Dict[str, Any]]:
            path = job_dir / "reports" / "isolation.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            if not data.get("passed"):
                raise RuntimeError(f"隔离测试失败: {data}")
            return data, {"log_artifacts": [str(path)]}

        self.orch.machine.run_state(State.ISOLATION_TEST, {"install": "ok"}, isolation_runner)

    def publish_report(self) -> None:
        def runner(_input: Any) -> Tuple[Dict[str, Any], Dict[str, Any]]:
            rpms = sorted(
                path for path in (self.orch.job_dir / "rpms").glob("*.rpm")
            )
            lock = SourceLock.load(self.orch.job_dir / "source.lock.json").to_dict()
            sbom = build_sbom(rpms)
            provenance = build_provenance(
                self.orch.config.name,
                self.orch.fingerprint(),
                lock,
                self.orch.config.build_image,
                rpms,
                extra={"runtime_strategy": self.orch.config.toolset.runtime_strategy},
            )
            reports = self.orch.job_dir / "reports"
            write_json(sbom, reports / "sbom.json")
            write_json(provenance, reports / "provenance.json")
            summary = {
                "job": self.orch.config.name,
                "fingerprint": self.orch.fingerprint(),
                "packages": [path.name for path in rpms],
                "states": self.orch.machine.summary(),
            }
            write_json(summary, reports / "summary.json")
            return summary, {"log_artifacts": [
                str(reports / "sbom.json"),
                str(reports / "provenance.json"),
                str(reports / "summary.json"),
            ]}

        self.orch.machine.run_state(
            State.PUBLISH_REPORT, {"verify": "ok"}, runner
        )

    def run_all(self) -> None:
        self.mark_approval()
        self.patch_transform()
        self.build()
        self.stage_install()
        self.generate_rpm()
        self.install_and_test()
        self.publish_report()
