"""错误分类与自动修复预算（方案 18）。

每个错误码携带：是否允许自动修复、最大修复次数、修复策略描述。
自动修复超出预算或遇到不可修复错误码时必须进入人工审批或冻结。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass(frozen=True)
class ErrorClass:
    code: str
    description: str
    auto_repair: Optional[str]   # None 表示禁止自动修复
    max_attempts: int


ERROR_CLASSES: Dict[str, ErrorClass] = {
    e.code: e for e in [
        ErrorClass("E-SOURCE-HASH", "源码哈希不符", None, 0),
        ErrorClass("E-SOURCE-MISSING", "源不可用", "镜像重试", 3),
        ErrorClass("E-PATCH-CONTEXT", "patch 上下文变化", "仅零 fuzz 规范化", 1),
        ErrorClass("E-PATCH-SEMANTIC", "语义冲突", None, 0),
        ErrorClass("E-BOOTSTRAP", "基础编译器不足", None, 0),
        ErrorClass("E-CONFIGURE", "configure 失败", "已知参数规则", 2),
        ErrorClass("E-AS-FEATURE", "assembler 能力缺失", "切换合格 binutils", 1),
        ErrorClass("E-LD-FEATURE", "linker 能力缺失", "切换合格 binutils", 1),
        ErrorClass("E-LTO-PLUGIN", "插件版本/路径错误", "路径修复", 2),
        ErrorClass("E-GLIBC-BASELINE", "glibc 要求过高", None, 0),
        ErrorClass("E-ABI-SYMBOL", "ABI 符号缺失", None, 0),
        ErrorClass("E-DUAL-ABI", "dual ABI 不一致", "提议重编 flags", 1),
        ErrorClass("E-RUNTIME-PATH", "loader 路径错误", "wrapper/RUNPATH 修正", 2),
        ErrorClass("E-NONSHARED-MISSING", "nonshared 缺失", None, 0),
        ErrorClass("E-NONSHARED-MISMATCH", "nonshared 版本/架构不符", None, 0),
        ErrorClass("E-NONSHARED-INCOMPLETE", "nonshared 差集不完整", None, 0),
        ErrorClass("E-MANIFEST", "文件遗漏/重复", "重新发现", 2),
        ErrorClass("E-RPM-DEPS", "依赖闭包错误", "明确 BuildRequires diff", 2),
        ErrorClass("E-FILE-CONFLICT", "RPM 文件冲突", "路径/归属修复", 1),
        ErrorClass("E-ISOLATION", "系统污染", None, 0),
        ErrorClass("E-REPRODUCIBLE", "两次构建差异", "时间戳/路径规则", 2),
        ErrorClass("E-SIGNING", "签名失败", None, 0),
        ErrorClass("E-POLICY", "策略禁止", None, 0),
    ]
}


class RepairBudgetExceeded(RuntimeError):
    pass


@dataclass
class RepairRecord:
    repair_id: str
    error_code: str
    reason: str
    evidence: List[str] = field(default_factory=list)
    proposal_diff: str = ""
    risk: str = "medium"
    before_result: str = "FAIL"
    after_result: str = ""
    approved_by: str = ""


class RepairLedger:
    """记录每个错误码已消耗的自动修复次数并执行预算。"""

    def __init__(self, global_max_auto_repairs: int = 2) -> None:
        self.global_max = global_max_auto_repairs
        self.records: List[RepairRecord] = []
        self._counts: Dict[str, int] = {}

    def can_auto_repair(self, error_code: str) -> bool:
        klass = ERROR_CLASSES.get(error_code)
        if klass is None or klass.auto_repair is None:
            return False
        if self._counts.get(error_code, 0) >= klass.max_attempts:
            return False
        if len(self.records) >= self.global_max and klass.code != "E-SOURCE-MISSING":
            return False
        return True

    def record(self, record: RepairRecord) -> None:
        if not self.can_auto_repair(record.error_code):
            raise RepairBudgetExceeded(
                f"错误码 {record.error_code} 不允许自动修复或已超出预算"
            )
        self._counts[record.error_code] = self._counts.get(record.error_code, 0) + 1
        record.repair_id = record.repair_id or f"R-{len(self.records) + 1:04d}"
        self.records.append(record)
