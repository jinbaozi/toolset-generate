"""gts-agent CLI（方案 8.3）。

子命令：
  discover / resolve-sources / analyze / plan / approve / build /
  verify / explain-failure / status
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from gts_agent import __version__
from gts_agent.agent.approvals import record_approval
from gts_agent.agent.orchestrator import Orchestrator
from gts_agent.core.models.config import ConfigError, load_job_config

DEFAULT_WORK_ROOT = Path("work")


def _load_orchestrator(args: argparse.Namespace) -> Orchestrator:
    config = load_job_config(Path(args.config))
    return Orchestrator(
        config,
        work_root=Path(args.work_root),
        policy_name=args.policy,
    )


def cmd_discover(args: argparse.Namespace) -> int:
    orch = _load_orchestrator(args)
    inventory = orch.discover()
    print(json.dumps(inventory, indent=2, ensure_ascii=False))
    return 0


def cmd_resolve_sources(args: argparse.Namespace) -> int:
    orch = _load_orchestrator(args)
    orch.discover()
    lock = orch.resolve_sources()
    print(f"source.lock 已写入: {orch.job_dir / 'source.lock.json'}")
    print(f"锁定源数量: {len(lock.sources)}")
    return 0


def cmd_analyze(args: argparse.Namespace) -> int:
    orch = _load_orchestrator(args)
    orch.discover()
    orch.resolve_sources()
    try:
        report = orch.analyze(run_binutils_probes=not args.skip_probes)
    except RuntimeError as exc:
        print(f"分析失败: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report.get("verdict") != "FAIL" else 2


def cmd_plan(args: argparse.Namespace) -> int:
    orch = _load_orchestrator(args)
    orch.discover()
    orch.resolve_sources()
    orch.analyze(run_binutils_probes=not args.skip_probes)
    plan_path = orch.generate_plan()
    print(f"build plan: {plan_path}")
    print(f"plan sha256: {orch.plan_sha256()}")
    print(f"job fingerprint: {orch.fingerprint()}")
    print(f"生成的 Spec 目录: {orch.job_dir / 'specs'}")
    return 0


def cmd_approve(args: argparse.Namespace) -> int:
    orch = _load_orchestrator(args)
    record = record_approval(
        job_dir=orch.job_dir,
        job_id=orch.config.name,
        plan_sha256=args.plan_sha256,
        decision=args.decision,
        approver=args.approver,
        scope=args.scope,
        comment=args.comment or "",
    )
    print(f"审批记录: {record.scope} {record.decision} by {record.approver}")
    if args.decision == "reject":
        from gts_agent.agent.state_machine import State
        orch.machine.freeze(State.APPROVAL, f"{args.scope} 被 {args.approver} 拒绝")
        return 3
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    orch = _load_orchestrator(args)
    try:
        orch.check_build_gate()
    except Exception as exc:
        print(f"构建被审批门阻断: {exc}", file=sys.stderr)
        return 3

    if not args.execute:
        from gts_agent.executors.mock import build_srpm_plan, execute_plan, mock_available
        spec = orch.job_dir / "specs" / f"gcc-toolset-{orch.config.toolset_id}-gcc.spec"
        plan = build_srpm_plan(
            mock_config=args.mock_config,
            spec_path=spec,
            sources_dir=orch.job_dir / "sources",
            result_dir=orch.job_dir / "rpms",
        )
        logs = execute_plan(plan, orch.job_dir / "logs", dry_run=True)
        print("dry-run：已生成构建计划。使用 --execute 在隔离容器中执行完整流水线。")
        for log in logs:
            print(f"  {log}")
        if orch.config.build_executor == "podman":
            print(f"将使用 Podman 镜像 {orch.config.build_image}")
        elif not mock_available():
            print("提示: 当前宿主未安装 mock。")
        return 0

    try:
        orch.run_pipeline()
    except Exception as exc:
        print(f"构建流水线失败: {exc}", file=sys.stderr)
        return 2
    print("流水线完成。报告目录:", orch.job_dir / "reports")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    orch = _load_orchestrator(args)
    from gts_agent.agent.pipeline import Pipeline
    try:
        pipeline = Pipeline(orch)
        pipeline.install_and_test()
        pipeline.publish_report()
    except Exception as exc:
        print(f"验证失败: {exc}", file=sys.stderr)
        return 2
    print("验证完成:", orch.job_dir / "reports" / "summary.json")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    orch = _load_orchestrator(args)
    for entry in orch.machine.summary():
        print(f"{entry['state']:<22} {entry['status']}")
    return 0


def cmd_explain_failure(args: argparse.Namespace) -> int:
    orch = _load_orchestrator(args)
    found = False
    for entry in orch.machine.summary():
        if entry["status"] in ("FAILED", "FROZEN"):
            found = True
            from gts_agent.agent.state_machine import PIPELINE, State
            state = State(entry["state"])
            record = orch.machine.load_latest(state)
            print(f"状态 {entry['state']} -> {entry['status']}")
            for diagnostic in record.diagnostics:
                print(f"  诊断: {json.dumps(diagnostic, ensure_ascii=False)}")
    if not found:
        print("没有失败或冻结的状态。")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gts-agent",
        description="GCC 多版本 Toolset 自动改造、构建、打包和验证智能体",
    )
    parser.add_argument("--version", action="version", version=f"gts-agent {__version__}")

    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--config", required=True, help="任务 YAML 配置")
        p.add_argument("--work-root", default=str(DEFAULT_WORK_ROOT))
        p.add_argument("--policy", default="default", help="策略名（default/production）")

    p = sub.add_parser("discover", help="探测宿主/构建根环境")
    add_common(p)
    p.set_defaults(func=cmd_discover)

    p = sub.add_parser("resolve-sources", help="生成 source.lock")
    add_common(p)
    p.set_defaults(func=cmd_resolve_sources)

    p = sub.add_parser("analyze", help="兼容性资格判定")
    add_common(p)
    p.add_argument("--skip-probes", action="store_true", help="跳过 binutils 实测探测")
    p.set_defaults(func=cmd_analyze)

    p = sub.add_parser("plan", help="生成 build plan 与 Spec")
    add_common(p)
    p.add_argument("--skip-probes", action="store_true")
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("approve", help="记录审批决定（绑定 plan 哈希）")
    add_common(p)
    p.add_argument("--plan-sha256", required=True)
    p.add_argument("--decision", required=True, choices=["approve", "reject"])
    p.add_argument("--approver", required=True)
    p.add_argument("--scope", default="build-plan",
                   choices=["build-plan", "patch", "private-runtime", "publish"])
    p.add_argument("--comment")
    p.set_defaults(func=cmd_approve)

    p = sub.add_parser("build", help="经过审批门后执行隔离构建流水线")
    add_common(p)
    p.add_argument("--mock-config", default="default")
    p.add_argument("--execute", action="store_true",
                   help="实际执行隔离构建（默认 dry-run）")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("verify", help="安装已构建 RPM 并运行验证矩阵")
    add_common(p)
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("status", help="查看状态机进度")
    add_common(p)
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("explain-failure", help="解释失败/冻结状态")
    add_common(p)
    p.set_defaults(func=cmd_explain_failure)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as exc:
        print(f"配置错误: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
