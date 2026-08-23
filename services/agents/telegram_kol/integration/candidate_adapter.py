from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from services.automated_trading.domain.candidates import CandidateLane, CandidateSide, TradeCandidate
from services.automated_trading.domain.enums import V2CandidateType

from ..domain.events import KolEventType, KolTradeEvent
from ..parsing.validator import validate_event


class KolCandidateAdapter:
    """Convert one validated KOL OPEN into a non-promotable V2 sampling candidate."""

    def to_candidate(
        self,
        event: KolTradeEvent,
        *,
        cycle_id: str,
        now: datetime,
        thread_id: str | None = None,
    ) -> TradeCandidate | None:
        if event.event_type is not KolEventType.OPEN:
            return None
        if len(event.take_profits) > 1:
            return None
        validation = validate_event(event, now=now)
        if not validation.accepted:
            return None
        if event.symbol is None or event.side not in {"LONG", "SHORT"}:
            return None
        reference = event.entry_price or event.entry_low
        if reference is None or event.stop_loss is None:
            return None
        stop_distance = abs(reference - event.stop_loss)
        tp_distance = abs(event.take_profits[0] - reference) if event.take_profits else None
        resolved_thread_id = thread_id or event.thread_id or f"{event.source_id}:{event.symbol}"
        candidate_key = f"telegram:{event.source_id}:{resolved_thread_id}:{event.message_id}:{event.revision}"
        context = (
            ("signal_source", "telegram_kol"),
            ("kol_source_id", event.source_id),
            ("thread_id", resolved_thread_id),
            ("telegram_message_id", str(event.message_id)),
            ("telegram_revision", str(event.revision)),
            ("entry_semantics", event.entry_semantics.value),
            ("claimed_leverage", str(event.claimed_leverage or "")),
            ("claimed_position_fraction", str(event.claimed_position_fraction or "")),
        )
        return TradeCandidate(
            candidate_id=candidate_key,
            cycle_id=cycle_id,
            strategy_id=f"telegram_kol:{event.source_id}",
            strategy_version="kol-parser-v1",
            lane=CandidateLane.TESTNET_SAMPLING,
            candidate_type=V2CandidateType.SAMPLING,
            symbol=event.symbol,
            side=CandidateSide.LONG if event.side == "LONG" else CandidateSide.SHORT,
            signal_candle_close_time=event.detected_at or now,
            signal_reference_price=reference,
            confidence=event.confidence,
            stop_distance=stop_distance,
            take_profit_distance=tp_distance,
            max_entry_drift_bps=Decimal("20"),
            expires_at=now + timedelta(minutes=15),
            non_promotable=True,
            signal_context=context,
        )
