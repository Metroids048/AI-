from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .models import KolTradeEvent
from .ocr import clean_ocr_text, extract_text_from_image, parse_ocr_blocks
from .parser import parse_message
from .thread import TradeThread, TradeThreadBook


@dataclass(frozen=True)
class MessageEnvelope:
    source_chat: str
    text: str | None = None
    image_path: Path | None = None


@dataclass
class PipelineResult:
    events: list[KolTradeEvent]
    threads: list[TradeThread]
    summary: dict[str, int]


def run_pipeline(messages: Iterable[MessageEnvelope]) -> PipelineResult:
    book = TradeThreadBook()
    events: list[KolTradeEvent] = []
    message_count = 0

    for message in messages:
        message_count += 1
        batch: list[KolTradeEvent]
        if message.image_path is not None:
            ocr_text = clean_ocr_text(extract_text_from_image(message.image_path))
            batch = list(parse_ocr_blocks(message.source_chat, ocr_text))
        else:
            raw_text = message.text or ""
            probe = parse_message(message.source_chat, raw_text)
            existing = book.get(message.source_chat, probe.symbol) if probe.symbol else None
            batch = [
                parse_message(
                    message.source_chat,
                    raw_text,
                    context_side=existing.side if existing is not None else None,
                )
            ]

        for event in batch:
            existing = book.get(event.source_chat, event.symbol) if event.symbol else None
            if existing is not None and event.event_type in {"POSITION_UPDATE", "ADD_POSITION", "STOP_REPORTED"}:
                if event.raw_text:
                    event = parse_message(event.source_chat, event.raw_text, context_side=existing.side)
            events.append(event)
            book.apply(event)

    counts: dict[str, int] = {
        "messages": message_count,
        "events": len(events),
        "open": 0,
        "position_update": 0,
        "add_position": 0,
        "stop_reported": 0,
        "commentary": 0,
        "result_report": 0,
        "advertisement": 0,
    }
    for event in events:
        key = event.event_type.lower()
        if key in counts:
            counts[key] += 1

    return PipelineResult(events=events, threads=list(book._threads.values()), summary=counts)
