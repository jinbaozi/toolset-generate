"""CompatibilityReport：兼容性资格判定结果（PASS / WARN / FAIL）。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List


class Verdict(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


class SupportState(str, Enum):
    QUALIFIED = "QUALIFIED"
    EXPERIMENTAL = "EXPERIMENTAL"
    UNSUPPORTED = "UNSUPPORTED"


@dataclass
class Finding:
    verdict: Verdict
    reason_code: str
    message: str
    facts: Dict[str, Any] = field(default_factory=dict)
    allowed_actions: List[str] = field(default_factory=list)
    forbidden_actions: List[str] = field(default_factory=list)


@dataclass
class CompatibilityReport:
    verdict: Verdict = Verdict.PASS
    support_state: SupportState = SupportState.EXPERIMENTAL
    findings: List[Finding] = field(default_factory=list)

    def add(self, finding: Finding) -> None:
        self.findings.append(finding)
        if finding.verdict == Verdict.FAIL:
            self.verdict = Verdict.FAIL
            self.support_state = SupportState.UNSUPPORTED
        elif finding.verdict == Verdict.WARN and self.verdict != Verdict.FAIL:
            self.verdict = Verdict.WARN

    @property
    def reason_codes(self) -> List[str]:
        return [f.reason_code for f in self.findings if f.verdict != Verdict.PASS]

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["verdict"] = self.verdict.value
        data["support_state"] = self.support_state.value
        for finding, raw in zip(self.findings, data["findings"]):
            raw["verdict"] = finding.verdict.value
        data["reason_codes"] = self.reason_codes
        return data

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
