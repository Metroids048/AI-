from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

from ..domain.events import Completeness, EntrySemantics, KolEventType, KolTradeEvent
from ..domain.messages import MessageEnvelope
from .normalizer import normalize_symbol

_NUMBER = r"\d+(?:\.\d+)?"
_SYMBOL = r"\b[A-Za-z]{2,12}(?:[/-]?USDT)?\b"


def _decimal(value: str) -> Decimal | None:
    try:
        return Decimal(value.replace(",", ""))
    except (InvalidOperation, ValueError):
        return None


class UniversalKolParser:
    """Small deterministic parser for common Chinese/English KOL phrasing."""

    def parse(self, message: MessageEnvelope) -> KolTradeEvent:
        text = message.normalized_text
        upper = text.upper()
        symbol = self._symbol(text)
        side = self._side(text)
        now = message.received_at

        if not text:
            return self._event(message, KolEventType.COMMENTARY, completeness=Completeness.NON_SIGNAL, detected_at=now)
        if self._contains_any(upper, ("广告", "群主", "开户链接", "欢迎加入")):
            return self._event(
                message,
                KolEventType.ADVERTISEMENT,
                symbol=symbol,
                completeness=Completeness.NON_SIGNAL,
                detected_at=now,
            )
        if self._has_direction_conflict(upper):
            return self._event(
                message,
                KolEventType.AMBIGUOUS,
                symbol=symbol,
                completeness=Completeness.NON_SIGNAL,
                detected_at=now,
                tags=("DIRECTION_CONFLICT",),
            )
        if self._contains_any(upper, ("撤单", "取消进场", "撤掉挂单", "CANCEL ENTRY")):
            return self._event(
                message,
                KolEventType.CANCEL_ENTRY,
                symbol=symbol,
                side=side,
                completeness=Completeness.MANAGEABLE,
                detected_at=now,
            )
        if self._contains_any(upper, ("止盈一半", "止盈50%", "减仓一半", "REDUCE 50%", "PARTIAL")):
            fraction = Decimal("0.5")
            match = re.search(rf"({_NUMBER})\s*%", upper)
            if match:
                fraction = (_decimal(match.group(1)) or Decimal("50")) / Decimal("100")
            return self._event(
                message,
                KolEventType.PARTIAL_CLOSE,
                symbol=symbol,
                side=side,
                close_fraction=fraction,
                completeness=Completeness.MANAGEABLE,
                detected_at=now,
            )
        if self._contains_any(upper, ("全部离场", "全部止盈", "全止盈", "CLOSE ALL", "EXIT ALL", "平仓")):
            return self._event(
                message,
                KolEventType.CLOSE_ALL,
                symbol=symbol,
                side=side,
                completeness=Completeness.MANAGEABLE,
                detected_at=now,
            )
        if self._contains_any(upper, ("移动止损", "保本损", "MOVE STOP", "止损移")):
            new_stop = self._labeled_number(upper, ("SL", "止损", "止损点位"))
            return self._event(
                message,
                KolEventType.MOVE_STOP,
                symbol=symbol,
                side=side,
                new_stop=new_stop,
                completeness=Completeness.MANAGEABLE,
                detected_at=now,
            )
        if self._contains_any(upper, ("撤掉止盈", "取消止盈", "CANCEL TP")):
            return self._event(
                message,
                KolEventType.CANCEL_TAKE_PROFIT,
                symbol=symbol,
                side=side,
                completeness=Completeness.MANAGEABLE,
                detected_at=now,
            )
        if self._contains_any(upper, ("设置止盈", "修改止盈", "SET TP", "TARGET UPDATE")):
            return self._event(
                message,
                KolEventType.SET_TAKE_PROFIT,
                symbol=symbol,
                side=side,
                take_profits=self._targets(upper),
                completeness=Completeness.MANAGEABLE,
                detected_at=now,
            )
        if self._contains_any(upper, ("加仓", "加一次仓", "补仓", "ADD POSITION", "ADD")) and not self._contains_any(
            upper, ("广告",)
        ):
            return self._event(
                message,
                KolEventType.ADD_POSITION,
                symbol=symbol,
                side=side,
                completeness=Completeness.MANAGEABLE,
                detected_at=now,
            )
        if self._contains_any(upper, ("止损已触发", "止损了", "STOP HIT")):
            return self._event(
                message,
                KolEventType.STOP_REPORTED,
                symbol=symbol,
                side=side,
                completeness=Completeness.MANAGEABLE,
                detected_at=now,
            )
        if self._contains_any(upper, ("结果", "收益", "盈利", "RESULT", "PNL")):
            return self._event(
                message,
                KolEventType.RESULT_REPORT,
                symbol=symbol,
                side=side,
                completeness=Completeness.MANAGEABLE,
                detected_at=now,
            )

        if self._looks_like_commentary(upper) and not side:
            return self._event(
                message, KolEventType.COMMENTARY, symbol=symbol, completeness=Completeness.NON_SIGNAL, detected_at=now
            )

        entry_semantics, entry_price, entry_low, entry_high = self._entry(text, upper)
        stop = self._labeled_number(upper, ("SL", "止损", "止损点位", "预计止损"))
        targets = self._targets(upper)
        leverage = self._labeled_number(upper, ("X", "倍", "LEVERAGE"))
        position_fraction = self._percent_claim(upper, ("保证金", "仓位", "POSITION"))
        if symbol and side:
            completeness = (
                Completeness.COMPLETE if stop is not None and (entry_price or entry_low) else Completeness.INCOMPLETE
            )
            if stop is not None and not (entry_price or entry_low):
                completeness = Completeness.MANAGEABLE
            return self._event(
                message,
                KolEventType.OPEN,
                symbol=symbol,
                side=side,
                entry_semantics=entry_semantics,
                entry_price=entry_price,
                entry_low=entry_low,
                entry_high=entry_high,
                stop_loss=stop,
                take_profits=targets,
                claimed_leverage=leverage,
                claimed_position_fraction=position_fraction,
                completeness=completeness,
                detected_at=now,
            )
        return self._event(
            message,
            KolEventType.COMMENTARY,
            symbol=symbol,
            side=side,
            completeness=Completeness.NON_SIGNAL,
            detected_at=now,
        )

    @staticmethod
    def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
        return any(needle in text for needle in needles)

    @staticmethod
    def _looks_like_commentary(text: str) -> bool:
        return any(token in text for token in ("还有机会", "回踩", "看多", "看空", "EXPECT", "MAY PULLBACK"))

    @staticmethod
    def _symbol(text: str) -> str | None:
        for candidate in re.findall(_SYMBOL, text):
            symbol = normalize_symbol(candidate)
            if symbol:
                return symbol
        return None

    @staticmethod
    def _side(text: str) -> str | None:
        long_hit = bool(re.search(r"做多|多头|(?<![看利])多(?!空)|\bLONG\b|\bBUY\b", text, re.I))
        short_hit = bool(re.search(r"做空|空头|空(?!头)|\bSHORT\b|\bSELL\b", text, re.I))
        if long_hit and short_hit:
            return None
        if long_hit:
            return "LONG"
        if short_hit:
            return "SHORT"
        return None

    @staticmethod
    def _has_direction_conflict(text: str) -> bool:
        if "多空" in text or "LONG/SHORT" in text or "LONG SHORT" in text:
            return True
        long_hit = bool(re.search(r"做多|多头|(?<![看利])多(?!空)|\bLONG\b|\bBUY\b", text, re.I))
        short_hit = bool(re.search(r"做空|空头|空(?!头)|\bSHORT\b|\bSELL\b", text, re.I))
        return long_hit and short_hit

    @staticmethod
    def _entry(text: str, upper: str) -> tuple[EntrySemantics, Decimal | None, Decimal | None, Decimal | None]:
        range_match = re.search(rf"({_NUMBER})\s*(?:-|~|至)\s*({_NUMBER})", upper)
        if range_match:
            return EntrySemantics.RANGE, None, _decimal(range_match.group(1)), _decimal(range_match.group(2))
        conditional = re.search(rf"(?:突破|BREAKOUT)\s*({_NUMBER})", upper)
        if conditional:
            return EntrySemantics.CONDITIONAL, _decimal(conditional.group(1)), None, None
        near = re.search(rf"({_NUMBER})\s*(?:附近|左右|NEAR)", upper)
        if near:
            return EntrySemantics.NEAR_PRICE, _decimal(near.group(1)), None, None
        limit = re.search(rf"({_NUMBER})\s*(?:挂|挂单|LIMIT)", upper)
        if limit:
            return EntrySemantics.LIMIT, _decimal(limit.group(1)), None, None
        labeled = re.search(rf"(?:现价|市价|MARKET)\s*(?:多|空|做多|做空)?\s*({_NUMBER})?", upper)
        if labeled and labeled.group(1):
            return EntrySemantics.MARKET, _decimal(labeled.group(1)), None, None
        numbers = re.findall(_NUMBER, upper)
        if numbers:
            return EntrySemantics.MARKET, _decimal(numbers[0]), None, None
        return EntrySemantics.UNKNOWN, None, None, None

    @staticmethod
    def _labeled_number(text: str, labels: tuple[str, ...]) -> Decimal | None:
        label_pattern = "|".join(re.escape(label) for label in labels)
        match = re.search(rf"(?:{label_pattern})\s*[:：=@]?\s*({_NUMBER})", text)
        return _decimal(match.group(1)) if match else None

    @staticmethod
    def _targets(text: str) -> tuple[Decimal, ...]:
        match = re.search(r"(?:TP|止盈|目标|TARGET)[^0-9]*((?:\d+(?:\.\d+)?\s*/\s*)*\d+(?:\.\d+)?)", text)
        if not match:
            return ()
        return tuple(value for part in match.group(1).split("/") if (value := _decimal(part.strip())) is not None)

    @staticmethod
    def _percent_claim(text: str, labels: tuple[str, ...]) -> Decimal | None:
        label_pattern = "|".join(re.escape(label) for label in labels)
        match = re.search(
            rf"({_NUMBER})\s*%\s*(?:{label_pattern})?|(?:{label_pattern})\s*[:：]?\s*({_NUMBER})\s*%", text
        )
        raw = match.group(1) or match.group(2) if match else None
        parsed = _decimal(raw) if raw else None
        return parsed / Decimal("100") if parsed is not None else None

    @staticmethod
    def _event(message: MessageEnvelope, event_type: KolEventType, **kwargs: Any) -> KolTradeEvent:
        return KolTradeEvent(
            source_id=message.source_id,
            chat_id=message.chat_id,
            message_id=message.message_id,
            revision=message.revision,
            raw_text=message.normalized_text,
            event_type=event_type,
            **kwargs,
        )
