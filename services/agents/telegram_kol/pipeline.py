from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .domain.messages import MessageEnvelope
from .domain.threads import ThreadLinkResult
from .ingestion.collector import TelegramCollector
from .integration.candidate_adapter import KolCandidateAdapter
from .integration.inbox import CandidateInbox, InboxItem
from .lifecycle.event_linker import TradeEventLinker
from .persistence.repository import TelegramKolRepository


@dataclass(frozen=True)
class PipelineResult:
    event: Any
    thread: ThreadLinkResult
    candidate_key: str | None
    inboxed: bool


class TelegramKolPipeline:
    """Raw -> parse -> thread -> safety -> inbox composition boundary."""

    def __init__(
        self,
        *,
        collector: TelegramCollector,
        linker: TradeEventLinker | None = None,
        adapter: KolCandidateAdapter | None = None,
        inbox: CandidateInbox | None = None,
        repository: TelegramKolRepository | None = None,
    ) -> None:
        self.collector = collector
        self.linker = linker or TradeEventLinker()
        self.adapter = adapter or KolCandidateAdapter()
        self.inbox = inbox or CandidateInbox()
        self.repository = repository

    def ingest(self, *, envelope: MessageEnvelope, cycle_id: str, now: datetime) -> PipelineResult:
        event = self.collector.ingest(
            source_id=envelope.source_id,
            chat_id=envelope.chat_id,
            message_id=envelope.message_id,
            posted_at=envelope.posted_at,
            received_at=envelope.received_at,
            text=envelope.text,
            caption=envelope.caption,
            media_path=envelope.media_path,
            media_hash=envelope.media_hash,
            reply_to_message_id=envelope.reply_to_message_id,
            revision=envelope.revision,
        )
        if event is None:
            return PipelineResult(None, ThreadLinkResult(None, "DUPLICATE_RAW"), None, False)
        thread = self.linker.link(event, envelope)
        if self.repository is not None and self.collector.last_raw_record is not None:
            raw_id = self.collector.last_raw_record.raw_id
            if raw_id:
                self.repository.save_event(raw_id=raw_id, event=event)
            if thread.thread is not None:
                self.repository.save_thread(thread.thread)
        candidate = self.adapter.to_candidate(
            event,
            cycle_id=cycle_id,
            now=now,
            thread_id=thread.thread.thread_id if thread.thread is not None else None,
        )
        if candidate is None:
            return PipelineResult(event, thread, None, False)
        context = dict(candidate.signal_context)
        payload = _candidate_payload(candidate)
        item = InboxItem(
            candidate_key=candidate.candidate_id,
            source_id=envelope.source_id,
            thread_id=str(context["thread_id"]),
            symbol=candidate.symbol,
            payload=payload,
            created_at=now,
        )
        inboxed = self.inbox.put(item)
        if self.repository is not None:
            inboxed = (
                self.repository.enqueue_candidate(
                    candidate_key=candidate.candidate_id,
                    source_id=envelope.source_id,
                    thread_id=str(context["thread_id"]),
                    symbol=candidate.symbol,
                    payload=payload,
                )
                and inboxed
            )
        return PipelineResult(event, thread, candidate.candidate_id, inboxed)


def _candidate_payload(candidate: Any) -> dict[str, Any]:
    def scalar(value: Any) -> str | None:
        return str(value) if value is not None else None

    return {
        "candidate_id": candidate.candidate_id,
        "cycle_id": candidate.cycle_id,
        "strategy_id": candidate.strategy_id,
        "strategy_version": candidate.strategy_version,
        "lane": str(candidate.lane),
        "candidate_type": str(candidate.candidate_type),
        "symbol": candidate.symbol,
        "side": str(candidate.side),
        "signal_candle_close_time": candidate.signal_candle_close_time.isoformat(),
        "signal_reference_price": scalar(candidate.signal_reference_price),
        "confidence": scalar(candidate.confidence),
        "stop_distance": scalar(candidate.stop_distance),
        "take_profit_distance": scalar(candidate.take_profit_distance),
        "max_entry_drift_bps": scalar(candidate.max_entry_drift_bps),
        "expires_at": candidate.expires_at.isoformat(),
        "non_promotable": candidate.non_promotable,
        "signal_context": dict(candidate.signal_context),
    }
