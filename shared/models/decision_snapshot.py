"""Per-cycle decision snapshot history.

Unlike ``PaperRuntimeStatus.last_cycle_decisions`` (which is rebuilt from
scratch and overwritten on every auto-cycle), rows created from this model
are appended incrementally so that decision-pipeline rejection reasons such
as ``technical_signals_insufficient`` or ``confirmation_unavailable_fail_closed``
can be tallied over a real historical window even though they never produce
an ``OrderExecution`` row.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from .base import PlatformModel


class DecisionSnapshot(PlatformModel):
    decision_snapshot_id: str | None = None
    paper_run_id: str
    symbol: str
    action: str = Field(description="PaperRuntimeAction.action, e.g. skip_no_trade_decision / open_long / hold_long")
    pipeline_status: str | None = Field(
        default=None, description="decision_trace['pipeline_status'], e.g. technical_signals_insufficient"
    )
    reason: str | None = None
    decision_trace: dict[str, Any] = Field(default_factory=dict)
    cycle_time: datetime
    created_at: datetime | None = None
