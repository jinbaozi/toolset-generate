import pytest

from gts_agent.agent.diagnostics import (
    ERROR_CLASSES,
    RepairBudgetExceeded,
    RepairLedger,
    RepairRecord,
)


def test_error_classes_cover_plan_table():
    for code in (
        "E-SOURCE-HASH", "E-PATCH-SEMANTIC", "E-BOOTSTRAP",
        "E-GLIBC-BASELINE", "E-ABI-SYMBOL", "E-NONSHARED-MISSING",
        "E-ISOLATION", "E-SIGNING", "E-POLICY",
    ):
        assert ERROR_CLASSES[code].auto_repair is None
        assert ERROR_CLASSES[code].max_attempts == 0


def test_non_repairable_blocked():
    ledger = RepairLedger()
    assert not ledger.can_auto_repair("E-GLIBC-BASELINE")
    with pytest.raises(RepairBudgetExceeded):
        ledger.record(RepairRecord(repair_id="", error_code="E-GLIBC-BASELINE", reason="x"))


def test_repair_budget_per_code():
    ledger = RepairLedger(global_max_auto_repairs=10)
    assert ledger.can_auto_repair("E-RUNTIME-PATH")  # max 2
    ledger.record(RepairRecord(repair_id="", error_code="E-RUNTIME-PATH", reason="a"))
    ledger.record(RepairRecord(repair_id="", error_code="E-RUNTIME-PATH", reason="b"))
    assert not ledger.can_auto_repair("E-RUNTIME-PATH")


def test_global_budget():
    ledger = RepairLedger(global_max_auto_repairs=1)
    ledger.record(RepairRecord(repair_id="", error_code="E-CONFIGURE", reason="a"))
    assert not ledger.can_auto_repair("E-MANIFEST")
