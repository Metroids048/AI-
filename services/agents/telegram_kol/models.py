from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class TakeProfit:
    price: Decimal
    close_fraction: Decimal | None = None


@dataclass(frozen=True)
class TradeAction:
    action_type: str
    price: Decimal | None = None
    fraction: Decimal | None = None


@dataclass
class KolTradeEvent:
    source_chat: str
    event_type: str
    symbol: str | None = None
    side: str | None = None
    entry_type: str = "UNKNOWN"
    entry_price: Decimal | None = None
    entry_low: Decimal | None = None
    entry_high: Decimal | None = None
    stop_loss: Decimal | None = None
    take_profits: list[TakeProfit] = field(default_factory=list)
    claimed_leverage: int | None = None
    claimed_position_fraction: Decimal | None = None
    actions: list[TradeAction] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    execution_readiness: str = "NOT_EXECUTABLE"
    raw_text: str = ""
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def to_dict(self) -> dict[str, Any]:
        def normalize(value: Any) -> Any:
            if isinstance(value, Decimal):
                return str(value)
            if isinstance(value, list):
                return [normalize(item) for item in value]
            if isinstance(value, dict):
                return {key: normalize(item) for key, item in value.items()}
            if hasattr(value, "__dataclass_fields__"):
                return normalize(asdict(value))
            return value

        return normalize(asdict(self))
