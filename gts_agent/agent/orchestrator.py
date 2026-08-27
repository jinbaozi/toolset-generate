"""Orchestrator：把配置、探测、分析、计划和审批串成可恢复流程。

MVP 中真正可端到端执行的阶段是：
Discover -> ResolveSources(锁定) -> AnalyzeCompatibility -> GeneratePlan -> Approval。
Build 及之后的阶段生成完整的 Mock 执行计划（executors.mock），
在具备 Mock/构建根的环境中执行；本模块自身绝不写宿主系统路径。
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any, Dict, List, Tuple

import yaml

from gts_agent import __version__
from gts_agent.adapters.distro import get_adapter
from gts_agent.agent.approvals import require_approval
from gts_agent.agent.policy_engine import (
    Policy,
    evaluate_fast_fail,
    load_policy,
)
from gts_agent.agent.state_machine import (
    JobStateMachine,
    State,
    job_fingerprint,
)
from gts_agent.core.compatibility.binutils import probe_binutils
from gts_agent.core.compatibility.gcc import analyze_gcc
from gts_agent.core.models.compatibility import Verdict
from gts_agent.core.models.config import JobConfig
from gts_agent.core.models.source_lock import (
    LockedPatch,
    LockedSource,
    SourceLock,
    sha256_file,
)
from gts_agent.core.probe import probe_host
from gts_agent.core.sources.srpm import SrpmError, index_spec_dir, inspect_srpm
from gts_agent.core.sources.tarball import fetch_tarball
from gts_agent.core.spec.renderer import render_template_file
from gts_agent.executors.podman import PodmanExecutor, image_exists

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates" / "spec"
_PKG_ROOT = Path(__file__).resolve().parent.parent
_WORKSPACE = _PKG_ROOT.parent


class Orchestrator:
    def __init__(self, config: JobConfig, work_root: Path, policy_name: str = "default"):
        self.config = config
        self.job_dir = work_root / config.name
        self.job_dir.mkdir(parents=True, exist_ok=True)
        self.machine = JobStateMachine(self.job_dir)
        self.policy: Policy = load_policy(policy_name)
        self.adapter = get_adapter(
            config.platform.distro.id, config.platform.distro.major
        )

    # ---------- Discover ----------

    def discover(self) -> Dict[str, Any]:
        def runner(_input: Any) -> Tuple[Dict[str, Any], Dict[str, Any]]:
            if self.config.build_executor == "podman":
                if not image_exists(self.config.build_image):
                    raise RuntimeError(
                        f"Podman 镜像 {self.config.build_image} 不存在"
                    )
                executor = PodmanExecutor(
                    self.config.build_image, src_root=_WORKSPACE
                )
                data = executor.probe_inventory(self.config.base_gcc.executable)
            else:
                inventory = probe_host(self.config.base_gcc.executable)
                data = inventory.to_dict()
            out = self.job_dir / "inventory.json"
            out.write_text(
                json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            return data, {"log_artifacts": [str(out)]}

        record = self.machine.run_state(
            State.DISCOVER, {"config": self.config.fingerprint_component()}, runner
        )
        return json.loads((self.job_dir / "inventory.json").read_text(encoding="utf-8"))

    # ---------- Resolve Sources ----------

    def resolve_sources(self) -> SourceLock:
        def runner(_input: Any) -> Tuple[Dict[str, Any], Dict[str, Any]]:
            sources_dir = self.job_dir / "sources"
            sources_dir.mkdir(parents=True, exist_ok=True)
            cache_dir = _WORKSPACE / "cache" / "sources"
            cache_dir.mkdir(parents=True, exist_ok=True)
            lock = SourceLock()
            empty_manifest = (
                _PKG_ROOT / "templates" / "patch-manifest.empty.yaml"
            )
            shutil.copy2(empty_manifest, sources_dir / "patch-manifest.yaml")
            raw_sources = self.config.raw.get("sources", {}) or {}
            allow_network = bool(raw_sources.get("allow_network", False))
            for name, source in self.config.sources.items():
                uri = _resolve_source_uri(source.uri, cache_dir)
                local_path = Path(uri)
                if local_path.exists() or (
                    (uri.startswith("http://") or uri.startswith("https://"))
                    and allow_network
                ):
                    local = fetch_tarball(uri, cache_dir, source.sha256)
                    dest = sources_dir / local.name
                    if local.resolve() != dest.resolve():
                        shutil.copy2(local, dest)
                    stored = str(dest)
                else:
                    stored = None
                lock.sources.append(LockedSource(
                    name=name,
                    type=source.type,
                    uri=source.uri,
                    sha256=source.sha256,
                    local_path=stored,
                ))
                if source.type == "srpm" and stored:
                    srpm_path = Path(stored)
                    extract_dir = sources_dir / f"{name}-srpm"
                    info = self._inspect_srpm_file(srpm_path, extract_dir)
                    lock.sources[-1].nevr = str(info.get("nevr") or "") or None
                    index_path = sources_dir / f"{name}-srpm-index.json"
                    index_path.write_text(
                        json.dumps(info, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8",
                    )
                    for spec_info in info.get("specs") or []:
                        for patch in spec_info.get("patches") or []:
                            filename = str(patch.get("filename") or "")
                            patch_path = extract_dir / filename
                            if not patch_path.is_file():
                                continue
                            lock.patches.append(LockedPatch(
                                id=patch_path.stem,
                                origin=name,
                                source_file=str(patch_path.relative_to(sources_dir)),
                                sha256=sha256_file(patch_path),
                                strip=1,
                                fuzz_allowed=0,
                            ))
            raw_sources = self.config.raw.get("sources", {}) or {}
            lock.source_date_epoch = raw_sources.get("source_date_epoch")
            repos = self.config.raw.get("repositories", {}) or {}
            lock.repo_snapshot_id = str(repos.get("snapshot_id", ""))
            lock.save(self.job_dir / "source.lock.json")
            return lock.to_dict(), {"log_artifacts": [str(self.job_dir / "source.lock.json")]}

        self.machine.run_state(
            State.RESOLVE_SOURCES,
            {"sources": {k: v.sha256 for k, v in self.config.sources.items()}},
            runner,
        )
        return SourceLock.load(self.job_dir / "source.lock.json")

    # ---------- Analyze ----------

    def analyze(self, run_binutils_probes: bool = True) -> Dict[str, Any]:
        inventory_path = self.job_dir / "inventory.json"
        if not inventory_path.exists():
            raise RuntimeError("缺少 inventory.json；请先运行 discover")
        inventory_data = json.loads(inventory_path.read_text(encoding="utf-8"))

        def runner(_input: Any) -> Tuple[Dict[str, Any], Dict[str, Any]]:
            from gts_agent.core.models.inventory import (
                BinutilsInfo, GccInfo, Inventory,
            )
            gcc_data = inventory_data.get("gcc") or {}
            binutils_data = inventory_data.get("binutils") or {}
            inventory = Inventory(
                **{k: v for k, v in inventory_data.items()
                   if k not in ("gcc", "binutils")},
            )
            inventory.gcc = GccInfo(**gcc_data) if gcc_data else None
            inventory.binutils = BinutilsInfo(**binutils_data) if binutils_data else None

            report = analyze_gcc(self.config, inventory)

            # 配置级策略快速失败
            policy_decisions = [
                {"rule": d.rule, "result": d.result, "detail": d.detail}
                for d in evaluate_fast_fail(self.config)
            ]
            denies = [d for d in policy_decisions if d["result"] == "DENY"]
            if denies:
                from gts_agent.core.models.compatibility import Finding
                for deny in denies:
                    report.add(Finding(
                        verdict=Verdict.FAIL,
                        reason_code="E-POLICY",
                        message=deny["detail"],
                    ))

            result: Dict[str, Any] = report.to_dict()

            if run_binutils_probes:
                probe_report = probe_binutils(self.config.base_gcc.executable)
                result["binutils_probes"] = probe_report.to_dict()
                if probe_report.failed:
                    result["verdict"] = Verdict.WARN.value \
                        if result["verdict"] == Verdict.PASS.value else result["verdict"]

            out = self.job_dir / "compatibility-report.json"
            out.write_text(
                json.dumps(result, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            if result["verdict"] == Verdict.FAIL.value:
                raise RuntimeError(
                    f"兼容性判定 FAIL：{result['reason_codes']}（详见 {out}）"
                )
            return result, {"policy_decisions": policy_decisions,
                            "log_artifacts": [str(out)]}

        self.machine.run_state(
            State.ANALYZE_COMPATIBILITY,
            {"inventory": inventory_data,
             "config": self.config.fingerprint_component()},
            runner,
        )
        return json.loads(
            (self.job_dir / "compatibility-report.json").read_text(encoding="utf-8")
        )

    # ---------- Plan ----------

    def generate_configure_flags(self) -> List[str]:
        flags = list(self.adapter.extra_configure_flags)
        flags.extend([
            "--enable-__cxa_atexit",
            "--enable-plugin",
            "--enable-linker-build-id",
            "--enable-checking=release",
            "--with-system-zlib",
            "--disable-multilib",
            "--disable-libsanitizer",
            "--disable-libquadmath",
            "--disable-libgomp",
            "--disable-libitm",
            "--disable-nls",
            f"--with-pkgversion=Internal GCC Toolset {self.config.toolset_id}",
        ])
        if self.config.target_gcc.bootstrap == "disable-bootstrap":
            flags.append("--disable-bootstrap")
        return flags

    def generate_plan(self) -> Path:
        source_lock_path = self.job_dir / "source.lock.json"
        compat_path = self.job_dir / "compatibility-report.json"
        for required in (source_lock_path, compat_path):
            if not required.exists():
                raise RuntimeError(f"缺少 {required.name}；请先完成前序阶段")

        def runner(_input: Any) -> Tuple[Dict[str, Any], Dict[str, Any]]:
            plan_dir = self.job_dir / "plan"
            plan_dir.mkdir(parents=True, exist_ok=True)

            configure_flags = self.generate_configure_flags()
            languages = ",".join(
                "c++" if lang == "cxx" else lang
                for lang in self.config.target_gcc.languages
            )
            lib_name = "lib64"
            glibc_baseline = self.config.platform.glibc_baseline or "2.34"
            bootstrap_target = (
                "profiledbootstrap"
                if self.config.target_gcc.bootstrap == "profiledbootstrap"
                else ("" if self.config.target_gcc.bootstrap == "disable-bootstrap"
                      else "bootstrap")
            )

            build_plan: Dict[str, Any] = {
                "schema_version": 1,
                "generator": f"gts-agent {__version__}",
                "job": self.config.name,
                "adapter": self.adapter.name,
                "spec_renderer": self.adapter.spec_renderer,
                "runtime_strategy": self.config.toolset.runtime_strategy,
                "packaging_layout": self.config.packaging_layout,
                "packages": self._package_graph(),
                "build_dag": [
                    "runtime-macro-package",
                    "seed-binutils(system-gcc)",
                    "target-gcc",
                    *(
                        ["final-binutils(toolset-gcc)"]
                        if self.config.binutils.rebuild_with_target_gcc else []
                    ),
                    "gcc-final-link-validation",
                ],
                "gcc": {
                    "version": self.config.target_gcc.version,
                    "bootstrap": self.config.target_gcc.bootstrap,
                    "languages": languages,
                    "configure_flags": configure_flags,
                    "out_of_tree": True,
                },
                "binutils": {
                    "version": self.config.binutils.version,
                    "rebuild_with_target_gcc": self.config.binutils.rebuild_with_target_gcc,
                },
                "toolset": {
                    "root": self.config.toolset.root,
                    "prefix": self.config.toolset.prefix,
                },
                "nonshared_baseline": (
                    self.adapter.nonshared_baseline
                    if self.config.toolset.runtime_strategy == "system-nonshared"
                    else None
                ),
            }
            plan_path = plan_dir / "build-plan.yaml"
            plan_path.write_text(
                yaml.safe_dump(build_plan, sort_keys=False, allow_unicode=True),
                encoding="utf-8",
            )

            # 渲染 GCC Spec（%files 使用精确清单占位；实际清单在 StageInstall 后生成）
            tokens = {
                "TOOLSET_ID": self.config.toolset_id,
                "GCC_MAJOR": str(self.config.target_gcc.major),
                "GCC_VERSION": self.config.target_gcc.version,
                "TARGET_TRIPLE": self.config.platform.target_triple,
                "RUNTIME_STRATEGY": self.config.toolset.runtime_strategy,
                "RELEASE": "1",
                "LICENSE_EXPRESSION": "GPL-3.0-or-later WITH GCC-exception-3.1",
                "PROJECT_URL": "https://example.internal/gts-agent",
                "SOURCE_VERSION": self.config.target_gcc.version,
                "SOURCE_DIR": f"gcc-{self.config.target_gcc.version}",
                "LANGUAGES": languages,
                "GCC_CONFIGURE_FLAGS": " \\\n    ".join(configure_flags),
                "BINUTILS_CONFIGURE_FLAGS": "",
                "BOOTSTRAP_TARGET": bootstrap_target,
                "VALIDATION_PROFILE": "production",
                "RUNTIME_EVR": f"{self.config.target_gcc.version}-1%{{?dist}}",
                "BINUTILS_EVR": f"{self.config.binutils.version}-1%{{?dist}}",
                "BINUTILS_MIN_EVR": f"{self.config.binutils.version}-1%{{?dist}}",
                "BINUTILS_VERSION": self.config.binutils.version,
                "RUNTIME_VERSION": self.config.target_gcc.version,
                "LIB_NAME": lib_name,
                "GLIBC_BASELINE": glibc_baseline,
                "CHANGELOG": (
                    "* Thu Aug 27 2026 GCC Toolset Agent "
                    "<gts-agent@example.internal> - 1\n"
                    f"- Initial generated spec for gcc-toolset-{self.config.toolset_id}"
                ),
            }
            specs_dir = self.job_dir / "specs"
            specs_dir.mkdir(parents=True, exist_ok=True)
            for template in ("gcc", "binutils", "runtime"):
                rendered = render_template_file(
                    _TEMPLATE_DIR / f"{template}.spec.in", tokens
                )
                (specs_dir / f"gcc-toolset-{self.config.toolset_id}-{template}.spec").write_text(
                    rendered, encoding="utf-8"
                )

            spec_index = index_spec_dir(specs_dir)
            spec_index_path = plan_dir / "spec-index.json"
            spec_index_path.write_text(
                json.dumps({"specs": spec_index}, indent=2, ensure_ascii=False)
                + "\n",
                encoding="utf-8",
            )

            plan_sha = hashlib.sha256(plan_path.read_bytes()).hexdigest()
            (plan_dir / "plan.sha256").write_text(plan_sha + "\n", encoding="utf-8")
            return build_plan, {
                "log_artifacts": [str(plan_path), str(spec_index_path)]
            }

        self.machine.run_state(
            State.GENERATE_PLAN,
            {
                "config": self.config.fingerprint_component(),
                "source_lock": source_lock_path.read_text(encoding="utf-8"),
                "templates": hashlib.sha256(
                    b"".join(
                        path.read_bytes()
                        for path in sorted(_TEMPLATE_DIR.glob("*.spec.in"))
                    )
                ).hexdigest(),
            },
            runner,
        )
        return self.job_dir / "plan" / "build-plan.yaml"

    def _package_graph(self) -> List[str]:
        toolset_id = self.config.toolset_id
        base = f"gcc-toolset-{toolset_id}"
        if self.config.packaging_layout == "strict-two-package":
            return [f"{base}-gcc", f"{base}-binutils"]
        packages = [
            f"{base}-runtime",
            f"{base}-binutils",
            f"{base}-gcc",
            f"{base}-gcc-c++",
            f"{base}-libstdc++-devel",
        ]
        if self.config.toolset.runtime_strategy == "private-runtime":
            packages.append(f"{base}-runtime-libs")
        return packages

    # ---------- Approval / Build gate ----------

    def _inspect_srpm_file(self, srpm_path: Path, extract_dir: Path) -> Dict[str, Any]:
        """解析输入 SRPM：宿主有 rpm 则本地查询，否则在构建容器中查询。"""
        try:
            return inspect_srpm(srpm_path, extract_dir)
        except SrpmError as exc:
            if "缺少" not in str(exc):
                raise
            if (
                self.config.build_executor != "podman"
                or not image_exists(self.config.build_image)
            ):
                raise
            return self._inspect_srpm_via_podman(srpm_path, extract_dir)

    def _inspect_srpm_via_podman(
        self, srpm_path: Path, extract_dir: Path
    ) -> Dict[str, Any]:
        executor = PodmanExecutor(self.config.build_image, src_root=_WORKSPACE)
        extract_dir.mkdir(parents=True, exist_ok=True)
        script = f"""
python3 - <<'PY'
import json, sys
from pathlib import Path
sys.path.insert(0, "/src")
from gts_agent.core.sources.srpm import inspect_srpm
info = inspect_srpm(Path("/srpm/{srpm_path.name}"), Path("/extract"))
Path("/extract/inspect.json").write_text(
    json.dumps(info, indent=2, ensure_ascii=False) + "\\n"
)
print("srpm-inspect-ok")
PY
"""
        result = executor.run(
            ["bash", "-lc", script],
            volumes=[
                f"{_WORKSPACE}:/src:ro",
                f"{srpm_path.parent.resolve()}:/srpm:ro",
                f"{extract_dir.resolve()}:/extract:rw",
            ],
            env={"PYTHONPATH": "/src"},
            workdir="/extract",
            timeout=300,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"容器内解析 SRPM 失败: {(result.stderr + result.stdout)[-800:]}"
            )
        return json.loads((extract_dir / "inspect.json").read_text(encoding="utf-8"))

    def plan_sha256(self) -> str:
        path = self.job_dir / "plan" / "plan.sha256"
        if not path.exists():
            raise RuntimeError("尚未生成 plan；请先运行 gts-agent plan")
        return path.read_text(encoding="utf-8").strip()

    def check_build_gate(self) -> None:
        """Build 之前的审批门：plan 审批 +（如需要）private-runtime 审批。"""
        plan_sha = self.plan_sha256()
        require_approval(self.job_dir, plan_sha, scope="build-plan")
        if (
            self.config.toolset.runtime_strategy == "private-runtime"
            and self.config.policy.require_private_runtime_approval
        ):
            require_approval(self.job_dir, plan_sha, scope="private-runtime")

    def fingerprint(self) -> str:
        components = {
            "canonical_config": self.config.fingerprint_component(),
            "policy_version": str(self.policy.data.get("version", "")),
            "adapter": self.adapter.name,
            "agent_version": __version__,
        }
        lock_path = self.job_dir / "source.lock.json"
        if lock_path.exists():
            components["source_lock"] = hashlib.sha256(
                lock_path.read_bytes()
            ).hexdigest()
        return job_fingerprint(components)

    def run_pipeline(self) -> None:
        from gts_agent.agent.pipeline import Pipeline
        Pipeline(self).run_all()


def _resolve_source_uri(uri: str, cache_dir: Path) -> str:
    path = Path(uri)
    if path.exists():
        return str(path.resolve())
    for root in (Path.cwd(), _WORKSPACE, cache_dir):
        candidate = root / uri
        if candidate.exists():
            return str(candidate.resolve())
        candidate = root / path.name
        if candidate.exists():
            return str(candidate.resolve())
    return uri
