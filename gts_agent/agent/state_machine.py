"""可恢复状态机（方案 6）。

- 状态输出只追加，不原地覆盖；
- 已成功且输入摘要未变化的状态直接复用（幂等与断点续建）；
- 每个状态记录持久化为 work/JOB_ID/states/<NN>-<State>.json。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional


class State(str, Enum):
    DISCOVER = "Discover"
    RESOLVE_SOURCES = "ResolveSources"
    ANALYZE_COMPATIBILITY = "AnalyzeCompatibility"
    GENERATE_PLAN = "GeneratePlan"
    APPROVAL = "Approval"
    PATCH_TRANSFORM = "PatchTransform"
    BUILD = "Build"
    STAGE_INSTALL = "StageInstall"
    GENERATE_RPM = "GenerateRPM"
    INSTALL_TEST = "InstallTest"
    COMPILE_LINK_TEST = "CompileLinkTest"
    ABI_SYMBOL_TEST = "AbiSymbolTest"
    ISOLATION_TEST = "IsolationTest"
    PUBLISH_REPORT = "PublishReport"


PIPELINE: List[State] = list(State)


class StateStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    FROZEN = "FROZEN"           # 审批被拒或策略阻断


class TransitionError(RuntimeError):
    pass


@dataclass
class StateRecord:
    state: str
    status: str
    input_digest: str = ""
    output_digest: str = ""
    attempt: int = 1
    started_at: str = ""
    finished_at: str = ""
    commands: List[str] = field(default_factory=list)
    log_artifacts: List[str] = field(default_factory=list)
    diagnostics: List[Dict[str, Any]] = field(default_factory=list)
    policy_decisions: List[Dict[str, Any]] = field(default_factory=list)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest_of(data: Any) -> str:
    canonical = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class JobStateMachine:
    """按 PIPELINE 顺序推进的可恢复状态机。"""

    def __init__(self, job_dir: Path) -> None:
        self.job_dir = job_dir
        self.states_dir = job_dir / "states"
        self.states_dir.mkdir(parents=True, exist_ok=True)

    # ---------- 持久化 ----------

    def _record_path(self, state: State, attempt: int) -> Path:
        index = PIPELINE.index(state)
        return self.states_dir / f"{index:02d}-{state.value}.attempt{attempt}.json"

    def load_latest(self, state: State) -> Optional[StateRecord]:
        index = PIPELINE.index(state)
        candidates = sorted(self.states_dir.glob(f"{index:02d}-{state.value}.attempt*.json"))
        if not candidates:
            return None
        data = json.loads(candidates[-1].read_text(encoding="utf-8"))
        return StateRecord(**data)

    def _save(self, state: State, record: StateRecord) -> None:
        path = self._record_path(state, record.attempt)
        # 只追加：同一 attempt 文件已存在且状态为终态时不允许覆盖
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
            if existing.get("status") in (StateStatus.SUCCEEDED, StateStatus.FROZEN):
                raise TransitionError(
                    f"状态 {state.value} attempt {record.attempt} 已是终态，输出不可覆盖"
                )
        path.write_text(
            json.dumps(asdict(record), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    # ---------- 推进 ----------

    def can_enter(self, state: State) -> bool:
        index = PIPELINE.index(state)
        for previous in PIPELINE[:index]:
            record = self.load_latest(previous)
            if record is None or record.status != StateStatus.SUCCEEDED:
                return False
        return True

    def run_state(self, state: State, input_data: Any, runner) -> StateRecord:
        """执行一个状态。runner(input_data) -> (output_data, extra_dict)。

        若上次成功且输入摘要一致，直接复用（不重复执行）。
        """
        if state != State.DISCOVER and not self.can_enter(state):
            raise TransitionError(
                f"不能进入 {state.value}：前序状态未全部成功"
            )

        input_digest = digest_of(input_data)
        latest = self.load_latest(state)
        if (
            latest is not None
            and latest.status == StateStatus.SUCCEEDED
            and latest.input_digest == input_digest
        ):
            return latest
        if latest is not None and latest.status == StateStatus.FROZEN:
            raise TransitionError(f"状态 {state.value} 已冻结（审批被拒或策略阻断）")

        attempt = (latest.attempt + 1) if latest is not None else 1
        record = StateRecord(
            state=state.value,
            status=StateStatus.RUNNING,
            input_digest=input_digest,
            attempt=attempt,
            started_at=_now(),
        )
        try:
            output_data, extra = runner(input_data)
            record.output_digest = digest_of(output_data)
            record.status = StateStatus.SUCCEEDED
            for key in ("commands", "log_artifacts", "diagnostics", "policy_decisions"):
                if key in (extra or {}):
                    setattr(record, key, extra[key])
        except Exception as exc:
            record.status = StateStatus.FAILED
            record.diagnostics.append({"error": str(exc), "type": type(exc).__name__})
            record.finished_at = _now()
            self._save(state, record)
            raise
        record.finished_at = _now()
        self._save(state, record)
        return record

    def freeze(self, state: State, reason: str) -> None:
        latest = self.load_latest(state)
        attempt = (latest.attempt + 1) if latest is not None else 1
        record = StateRecord(
            state=state.value,
            status=StateStatus.FROZEN,
            attempt=attempt,
            started_at=_now(),
            finished_at=_now(),
            diagnostics=[{"reason": reason}],
        )
        self._save(state, record)

    def summary(self) -> List[Dict[str, str]]:
        result = []
        for state in PIPELINE:
            record = self.load_latest(state)
            result.append({
                "state": state.value,
                "status": record.status if record else StateStatus.PENDING.value,
            })
        return result


def job_fingerprint(components: Dict[str, str]) -> str:
    """任务指纹（方案 6.3）：所有输入组件哈希的哈希。"""
    canonical = json.dumps(components, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
