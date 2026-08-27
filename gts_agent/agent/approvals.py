"""审批控制模块：审批记录绑定 plan 哈希，拒绝则任务冻结（方案 6.1）。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


class ApprovalError(RuntimeError):
    pass


@dataclass
class ApprovalRecord:
    job_id: str
    plan_sha256: str
    decision: str          # approve / reject
    approver: str
    scope: str = "build-plan"   # build-plan / patch / private-runtime / publish
    comment: str = ""
    created_at: str = ""

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(asdict(self), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )


def record_approval(
    job_dir: Path,
    job_id: str,
    plan_sha256: str,
    decision: str,
    approver: str,
    scope: str = "build-plan",
    comment: str = "",
) -> ApprovalRecord:
    if decision not in ("approve", "reject"):
        raise ApprovalError(f"decision 必须是 approve/reject，收到 {decision!r}")
    if not plan_sha256:
        raise ApprovalError("审批必须绑定 plan 的 SHA-256")
    if not approver:
        raise ApprovalError("审批必须指定 approver")

    record = ApprovalRecord(
        job_id=job_id,
        plan_sha256=plan_sha256,
        decision=decision,
        approver=approver,
        scope=scope,
        comment=comment,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    record.save(job_dir / "approvals" / f"{scope}.{decision}.json")
    return record


def find_approval(
    job_dir: Path, scope: str = "build-plan"
) -> Optional[ApprovalRecord]:
    approve_path = job_dir / "approvals" / f"{scope}.approve.json"
    reject_path = job_dir / "approvals" / f"{scope}.reject.json"
    # 拒绝优先：一旦拒绝，任务冻结
    for path in (reject_path, approve_path):
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            return ApprovalRecord(**data)
    return None


def require_approval(job_dir: Path, plan_sha256: str, scope: str = "build-plan") -> ApprovalRecord:
    record = find_approval(job_dir, scope)
    if record is None:
        raise ApprovalError(f"缺少 {scope} 审批记录；请先运行 gts-agent approve")
    if record.decision != "approve":
        raise ApprovalError(f"{scope} 审批被拒绝（approver={record.approver}），任务冻结")
    if record.plan_sha256 != plan_sha256:
        raise ApprovalError(
            f"审批绑定的 plan 哈希 {record.plan_sha256[:16]}... 与当前 plan "
            f"{plan_sha256[:16]}... 不一致；plan 变更后必须重新审批"
        )
    return record
