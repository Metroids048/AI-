from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from ..domain.events import Completeness, EntrySemantics, KolEventType, KolTradeEvent


@dataclass(frozen=True)
class ValidationResult:
    accepted: bool
    reason_code: str
    detail: str = ""


def validate_event(event: KolTradeEvent, *, now: datetime, max_age_seconds: int = 900) -> ValidationResult:
    if event.event_type is not KolEventType.OPEN:
        return ValidationResult(
            event.completeness is not Completeness.NON_SIGNAL,
            "NON_SIGNAL" if event.completeness is Completeness.NON_SIGNAL else "MANAGEMENT_EVENT",
        )
    if event.symbol is None or event.side not in {"LONG", "SHORT"}:
        return ValidationResult(False, "INVALID_DIRECTION_OR_SYMBOL")
    if event.stop_loss is None:
        return ValidationResult(False, "MISSING_STOP")
    entry = event.entry_price or event.entry_low
    if entry is None or entry <= 0:
        return ValidationResult(False, "MISSING_ENTRY")
    if event.entry_semantics is EntrySemantics.CONDITIONAL:
        return ValidationResult(False, "CONDITIONAL_TRIGGER_UNSUPPORTED")
    if event.side == "LONG" and event.stop_loss >= entry:
        return ValidationResult(False, "INVALID_RISK_GEOMETRY")
    if event.side == "SHORT" and event.stop_loss <= entry:
        return ValidationResult(False, "INVALID_RISK_GEOMETRY")
    if event.detected_at is not None:
        age = (now.astimezone(UTC) - event.detected_at.astimezone(UTC)).total_seconds()
        if age > max_age_seconds:
            return ValidationResult(False, "STALE_SIGNAL")
    if event.completeness is not Completeness.COMPLETE:
        return ValidationResult(False, "INCOMPLETE_SIGNAL")
    return ValidationResult(True, "VALID")
