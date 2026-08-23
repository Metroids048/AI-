from __future__ import annotations

import re
from decimal import Decimal

from .models import KolTradeEvent, TakeProfit, TradeAction

_SYMBOL_ALIASES = {
    "比特币": "BTC",
    "以太坊": "ETH",
}

_KNOWN_TICKERS = {
    "BTC", "ETH", "SOL", "XRP", "BNB", "DOGE", "ADA", "AVAX", "LINK", "DOT",
    "LTC", "BCH", "ZEC", "TRB", "TAO", "ROBO", "PENGU", "SUI", "TON", "APT",
    "ARB", "OP", "NEAR", "FIL", "ATOM", "UNI", "AAVE", "ETC", "XLM", "HBAR",
    "ICP", "SHIB", "PEPE", "WIF", "BONK", "ENA", "INJ", "SEI", "RUNE", "FET",
    "TIA", "JUP", "ONDO",
}

_OPEN_LONG = re.compile(r"(?:做多|开多|市价(?:直接)?多|市价入场做多|入场做多|直接多)(?!单的)", re.I)
_OPEN_SHORT = re.compile(r"(?:做空|开空|市价(?:直接)?空|市价入场做空|入场做空|直接空)(?!单的)", re.I)
_COMMENTARY_MARKERS = (
    "可以考虑",
    "可能",
    "预计会",
    "等待",
    "耐心",
    "思路",
    "观察",
    "阻力",
    "支撑位",
    "抄底现货",
    "重新接回",
)
_AD_MARKERS = ("QQ:", "电报联系", "会员续费", "不构成任何交易建议", "不承诺收益")


def _normalize(text: str) -> str:
    return (
        text.replace("：", ":")
        .replace("，", ",")
        .replace("。", ".")
        .replace("－", "-")
        .replace("—", "-")
        .replace("／", "/")
    )


def _decimal(value: str | None) -> Decimal | None:
    return Decimal(value) if value else None


def _symbol(text: str) -> str | None:
    normalized = text
    for chinese, ticker in _SYMBOL_ALIASES.items():
        normalized = normalized.replace(chinese, ticker)
    upper = normalized.upper()
    explicit_pair = re.search(r"(?<![A-Z0-9])([A-Z0-9]{2,15})USDT(?![A-Z0-9])", upper)
    if explicit_pair:
        return f"{explicit_pair.group(1)}/USDT"
    matches = re.findall(r"(?<![A-Z])([A-Z]{2,10})(?![A-Z])", upper)
    for token in matches:
        if token in _KNOWN_TICKERS:
            return f"{token}/USDT"
    return None


def _explicit_side(text: str) -> str | None:
    if _OPEN_LONG.search(text) or re.search(r"(?:方向|进行方向)\s*[:：]?\s*(?:做多|LONG)", text, re.I):
        return "LONG"
    if _OPEN_SHORT.search(text) or re.search(r"(?:方向|进行方向)\s*[:：]?\s*(?:做空|SHORT)", text, re.I):
        return "SHORT"
    if re.search(r"(?:多单|LONG)", text, re.I):
        return "LONG"
    if re.search(r"(?:空单|SHORT)", text, re.I):
        return "SHORT"
    return None


def _parse_entry(text: str) -> tuple[str, Decimal | None, Decimal | None, Decimal | None]:
    range_match = re.search(r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*(?:附近)?", text)
    if range_match and any(word in text for word in ("入场", "进场", "附近", "做多", "做空")):
        return "RANGE", None, _decimal(range_match.group(1)), _decimal(range_match.group(2))

    market_with_price = re.search(r"(?:入场|进场)\s*[:：]?\s*现价\s*(\d+(?:\.\d+)?)", text)
    if market_with_price:
        return "MARKET", _decimal(market_with_price.group(1)), None, None

    if "市价" in text or re.search(r"现价(?:直接)?(?:做多|做空|多|空)", text):
        ref = re.search(r"(\d+(?:\.\d+)?)\s*附近\s*市价", text)
        if ref is None:
            ref = re.search(r"(?:入场|进场)\s*[:：]?\s*现价\s*(\d+(?:\.\d+)?)", text)
        if ref is None:
            ref = re.search(r"(?:市价|现价)\s*(\d+(?:\.\d+)?)", text)
        return "MARKET", _decimal(ref.group(1)) if ref else None, None, None

    near_patterns = [
        r"(?:进场点位|入场点位|入场|进场)\s*[:：]?\s*(\d+(?:\.\d+)?)\s*附近",
        r"(\d+(?:\.\d+)?)\s*附近\s*(?:做多|做空)",
        r"(?:做多|做空)\s*(\d+(?:\.\d+)?)\s*附近",
    ]
    for pattern in near_patterns:
        match = re.search(pattern, text)
        if match:
            return "NEAR_PRICE", _decimal(match.group(1)), None, None

    return "UNKNOWN", None, None, None


def _stop_loss(text: str) -> Decimal | None:
    patterns = [
        r"止损(?:点位|预计|设|设置|放在)?\s*[:：]?\s*(\d+(?:\.\d+)?)",
        r"SL\s*[:：]?\s*(\d+(?:\.\d+)?)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return _decimal(match.group(1))
    return None


def _targets(text: str) -> list[TakeProfit]:
    candidates: list[TakeProfit] = []
    seen: set[Decimal] = set()
    for match in re.finditer(r"止盈(?:点位|预计)?\s*[:：]?\s*((?:\d+(?:\.\d+)?\s*[/,、]\s*)+\d+(?:\.\d+)?)", text):
        for number in re.findall(r"\d+(?:\.\d+)?", match.group(1)):
            price = Decimal(number)
            if price not in seen:
                seen.add(price)
                candidates.append(TakeProfit(price=price))

    for match in re.finditer(r"(?:TP\s*\d*|第一止盈|第二止盈|第三止盈|止盈点位)\s*[:：]?\s*(\d+(?:\.\d+)?)", text, re.I):
        price = Decimal(match.group(1))
        if price in seen:
            continue
        tail = text[match.end() : match.end() + 40]
        fraction = None
        pct = re.search(r"(?:止盈|平|减仓)?\s*(\d{1,3})%", tail)
        if pct:
            fraction = Decimal(pct.group(1)) / Decimal("100")
        elif re.search(r"全部止盈|全平|全部离场", tail):
            fraction = Decimal("1")
        candidates.append(TakeProfit(price=price, close_fraction=fraction))
        seen.add(price)

    for match in re.finditer(r"止盈(?:点位|预计)?\s*[:：]?\s*(\d+(?:\.\d+)?)", text):
        if text[match.end():match.end()+1] == "%":
            continue
        price = Decimal(match.group(1))
        if price not in seen:
            candidates.append(TakeProfit(price=price))
            seen.add(price)
    return candidates


def _management_actions(text: str) -> list[TradeAction]:
    actions: list[TradeAction] = []
    if re.search(r"(?:止盈|平仓|减仓)(?:一半|半仓)", text):
        actions.append(TradeAction("PARTIAL_CLOSE", fraction=Decimal("0.5")))
    else:
        pct = re.search(r"(?:止盈|平仓|减仓)\s*(\d{1,3})%", text)
        if pct:
            actions.append(TradeAction("PARTIAL_CLOSE", fraction=Decimal(pct.group(1)) / Decimal("100")))
    move = re.search(r"(?:剩余仓位)?止损(?:放在|改到|移动到|上移到)\s*(\d+(?:\.\d+)?)", text)
    if move:
        actions.append(TradeAction("MOVE_STOP", price=Decimal(move.group(1))))
    if re.search(r"(?:止盈|TP)(?:取消|撤销)", text, re.I):
        actions.append(TradeAction("CANCEL_TAKE_PROFIT"))
    if re.search(r"(?:全部离场|全部走|全部止盈|全平)", text):
        actions.append(TradeAction("CLOSE_ALL", fraction=Decimal("1")))
    return actions


def _planned_open_actions(text: str) -> list[TradeAction]:
    actions: list[TradeAction] = []
    for match in re.finditer(r"(?:再挂|挂单?)\s*(\d+(?:\.\d+)?)\s*(?:补仓|加仓)", text):
        actions.append(TradeAction("PLANNED_ADD", price=Decimal(match.group(1))))
    return actions


def _claimed_leverage(text: str) -> int | None:
    match = re.search(r"(\d{1,3})\s*(?:x|倍)", text, re.I)
    return int(match.group(1)) if match else None


def _claimed_fraction(text: str) -> Decimal | None:
    match = re.search(r"(\d{1,3}(?:\.\d+)?)%\s*(?:仓位|保证金)", text)
    return Decimal(match.group(1)) / Decimal("100") if match else None


def _looks_like_commentary(text: str) -> bool:
    if any(marker in text for marker in _AD_MARKERS) and not re.search(r"做多|做空|开多|开空|止损|止盈|加仓|补仓", text):
        return True
    if re.search(r"有开多单的可以考虑|有开空单的可以考虑", text):
        return True
    has_open_marker = _OPEN_LONG.search(text) or _OPEN_SHORT.search(text)
    if not has_open_marker and any(marker in text for marker in _COMMENTARY_MARKERS):
        return True
    return False


def _risk_geometry_warning(event: KolTradeEvent) -> None:
    reference = event.entry_price
    if reference is None and event.entry_low is not None and event.entry_high is not None:
        reference = (event.entry_low + event.entry_high) / Decimal("2")
    if reference is None or event.stop_loss is None or event.side is None:
        return
    if event.side == "LONG" and event.stop_loss >= reference:
        event.warnings.append("INVALID_RISK_GEOMETRY")
    if event.side == "SHORT" and event.stop_loss <= reference:
        event.warnings.append("INVALID_RISK_GEOMETRY")


def parse_message(source_chat: str, raw_text: str, *, context_side: str | None = None) -> KolTradeEvent:
    text = _normalize(raw_text)
    symbol = _symbol(text)
    explicit_side = _explicit_side(text)
    direct_open = bool(
        _OPEN_LONG.search(text)
        or _OPEN_SHORT.search(text)
        or re.search(r"(?:方向|进行方向)\s*[:：]?\s*(?:做多|做空|LONG|SHORT)", text, re.I)
    )
    actions = _management_actions(text)

    if actions and not direct_open:
        warnings: list[str] = []
        side = context_side or explicit_side
        if context_side and explicit_side and explicit_side != context_side:
            warnings.append("DIRECTION_CONTEXT_CONFLICT")
            side = context_side
        return KolTradeEvent(
            source_chat=source_chat,
            event_type="POSITION_UPDATE",
            symbol=symbol,
            side=side,
            actions=actions,
            warnings=warnings,
            execution_readiness="MANAGEMENT",
            raw_text=raw_text,
        )

    if not direct_open and re.search(r"(?:现价)?加(?:一次)?仓|补仓", text):
        entry_type = "MARKET" if "现价" in text else "UNKNOWN"
        return KolTradeEvent(
            source_chat=source_chat,
            event_type="ADD_POSITION",
            symbol=symbol,
            side=context_side or explicit_side,
            entry_type=entry_type,
            missing_fields=["add_position_size"],
            execution_readiness="INCOMPLETE",
            raw_text=raw_text,
        )

    if re.search(r"止损被插针|止损了|止损触发|止损出局", text):
        return KolTradeEvent(
            source_chat=source_chat,
            event_type="STOP_REPORTED",
            symbol=symbol,
            side=context_side or explicit_side,
            execution_readiness="NOT_EXECUTABLE",
            raw_text=raw_text,
        )

    if _looks_like_commentary(text):
        return KolTradeEvent(
            source_chat=source_chat,
            event_type="COMMENTARY",
            symbol=symbol,
            side=context_side,
            execution_readiness="NOT_EXECUTABLE",
            raw_text=raw_text,
        )

    if direct_open:
        side = explicit_side
        entry_type, entry_price, entry_low, entry_high = _parse_entry(text)
        stop = _stop_loss(text)
        targets = _targets(text)
        missing: list[str] = []
        if side is None:
            missing.append("side")
        if entry_type == "UNKNOWN":
            missing.append("entry")
        if stop is None:
            missing.append("stop_loss")
        if not targets:
            missing.append("take_profit")
        event = KolTradeEvent(
            source_chat=source_chat,
            event_type="OPEN",
            symbol=symbol,
            side=side,
            entry_type=entry_type,
            entry_price=entry_price,
            entry_low=entry_low,
            entry_high=entry_high,
            stop_loss=stop,
            take_profits=targets,
            claimed_leverage=_claimed_leverage(text),
            claimed_position_fraction=_claimed_fraction(text),
            actions=_planned_open_actions(text),
            missing_fields=missing,
            execution_readiness="COMPLETE" if not missing else "INCOMPLETE",
            raw_text=raw_text,
        )
        _risk_geometry_warning(event)
        if "INVALID_RISK_GEOMETRY" in event.warnings:
            event.execution_readiness = "NEEDS_REVIEW"
        return event

    if re.search(r"获利\d|连胜|盈利\d", text):
        event_type = "RESULT_REPORT"
    elif any(marker in text for marker in _AD_MARKERS):
        event_type = "ADVERTISEMENT"
    else:
        event_type = "COMMENTARY"
    return KolTradeEvent(
        source_chat=source_chat,
        event_type=event_type,
        symbol=symbol,
        side=context_side,
        execution_readiness="NOT_EXECUTABLE",
        raw_text=raw_text,
    )
