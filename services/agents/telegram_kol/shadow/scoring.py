from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal


@dataclass
class ForwardSourceScore:
    source_id: str
    collector_start_at: str
    signals: int = 0
    parse_success: int = 0
    ambiguous: int = 0
    complete: int = 0
    entry_missed: int = 0
    realized_r: list[Decimal] = field(default_factory=list)

    def record_signal(self, *, parsed: bool, is_complete: bool, ambiguous: bool = False) -> None:
        self.signals += 1
        self.parse_success += int(parsed)
        self.complete += int(is_complete)
        self.ambiguous += int(ambiguous)

    @property
    def parse_success_rate(self) -> Decimal:
        return Decimal(self.parse_success) / Decimal(self.signals) if self.signals else Decimal("0")

    @property
    def expectancy_r(self) -> Decimal:
        return sum(self.realized_r, Decimal("0")) / Decimal(len(self.realized_r)) if self.realized_r else Decimal("0")
