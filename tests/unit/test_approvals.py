import pytest

from gts_agent.agent.approvals import (
    ApprovalError,
    find_approval,
    record_approval,
    require_approval,
)


def test_record_and_require(tmp_path):
    record_approval(tmp_path, "job", "abc123", "approve", "release-engineer")
    record = require_approval(tmp_path, "abc123")
    assert record.approver == "release-engineer"


def test_missing_approval_blocks(tmp_path):
    with pytest.raises(ApprovalError):
        require_approval(tmp_path, "abc123")


def test_plan_hash_mismatch_blocks(tmp_path):
    record_approval(tmp_path, "job", "abc123", "approve", "eng")
    with pytest.raises(ApprovalError) as exc:
        require_approval(tmp_path, "different-hash")
    assert "重新审批" in str(exc.value)


def test_reject_freezes(tmp_path):
    record_approval(tmp_path, "job", "abc123", "approve", "eng")
    record_approval(tmp_path, "job", "abc123", "reject", "security")
    # 拒绝优先
    record = find_approval(tmp_path)
    assert record.decision == "reject"
    with pytest.raises(ApprovalError):
        require_approval(tmp_path, "abc123")


def test_invalid_decision(tmp_path):
    with pytest.raises(ApprovalError):
        record_approval(tmp_path, "job", "abc123", "maybe", "eng")
