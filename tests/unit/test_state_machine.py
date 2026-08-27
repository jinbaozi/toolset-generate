import pytest

from gts_agent.agent.state_machine import (
    JobStateMachine,
    State,
    StateStatus,
    TransitionError,
)


def _ok_runner(input_data):
    return {"result": "ok"}, {}


def test_run_state_success(tmp_path):
    machine = JobStateMachine(tmp_path)
    record = machine.run_state(State.DISCOVER, {"config": "x"}, _ok_runner)
    assert record.status == StateStatus.SUCCEEDED
    assert record.input_digest.startswith("sha256:")


def test_idempotent_reuse(tmp_path):
    machine = JobStateMachine(tmp_path)
    calls = []

    def runner(input_data):
        calls.append(1)
        return {"result": "ok"}, {}

    machine.run_state(State.DISCOVER, {"config": "x"}, runner)
    machine.run_state(State.DISCOVER, {"config": "x"}, runner)
    assert len(calls) == 1  # 输入未变，直接复用

    machine.run_state(State.DISCOVER, {"config": "y"}, runner)
    assert len(calls) == 2  # 输入变化，重新执行


def test_cannot_skip_states(tmp_path):
    machine = JobStateMachine(tmp_path)
    with pytest.raises(TransitionError):
        machine.run_state(State.BUILD, {}, _ok_runner)


def test_running_record_persisted_before_runner(tmp_path):
    machine = JobStateMachine(tmp_path)
    seen = {}

    def runner(input_data):
        latest = machine.load_latest(State.DISCOVER)
        seen["status"] = latest.status
        seen["attempt"] = latest.attempt
        return {"result": "ok"}, {}

    record = machine.run_state(State.DISCOVER, {"a": 1}, runner)
    assert seen["status"] == StateStatus.RUNNING
    assert seen["attempt"] == 1
    assert record.status == StateStatus.SUCCEEDED


def test_failed_state_recorded_and_retryable(tmp_path):
    machine = JobStateMachine(tmp_path)

    def failing(input_data):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        machine.run_state(State.DISCOVER, {"a": 1}, failing)
    record = machine.load_latest(State.DISCOVER)
    assert record.status == StateStatus.FAILED
    assert record.diagnostics[0]["error"] == "boom"

    # 修复后可重试，attempt 递增
    record = machine.run_state(State.DISCOVER, {"a": 1}, _ok_runner)
    assert record.status == StateStatus.SUCCEEDED
    assert record.attempt == 2


def test_frozen_state_blocks(tmp_path):
    machine = JobStateMachine(tmp_path)
    machine.run_state(State.DISCOVER, {"a": 1}, _ok_runner)
    machine.freeze(State.DISCOVER, "审批被拒")
    with pytest.raises(TransitionError):
        machine.run_state(State.DISCOVER, {"a": 2}, _ok_runner)


def test_summary(tmp_path):
    machine = JobStateMachine(tmp_path)
    machine.run_state(State.DISCOVER, {}, _ok_runner)
    summary = machine.summary()
    assert summary[0] == {"state": "Discover", "status": "SUCCEEDED"}
    assert summary[1]["status"] == "PENDING"
