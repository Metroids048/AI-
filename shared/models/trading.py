"""Immutable execution contracts shared across validation, strategy and execution layers."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import ConfigDict, Field, model_validator

from .base import PlatformModel


class ImmutableContract(PlatformModel):
    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        str_strip_whitespace=True,
        frozen=True,
    )


class PositionSide(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"
    FLAT = "FLAT"


class TradeAction(StrEnum):
    OPEN = "OPEN"
    CLOSE = "CLOSE"
    REDUCE = "REDUCE"


class ExchangeSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class RuntimeMode(StrEnum):
    BACKTEST = "BACKTEST"
    REPLAY = "REPLAY"
    SHADOW = "SHADOW"
    PAPER = "PAPER"
    LIVE = "LIVE"


class MarketRegime(StrEnum):
    BULL = "BULL"
    BEAR = "BEAR"
    RANGE = "RANGE"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    UNTRADABLE = "UNTRADABLE"


class ExecutionState(StrEnum):
    INTENT_CREATED = "INTENT_CREATED"
    NORMALIZED = "NORMALIZED"
    SUBMITTING = "SUBMITTING"
    SUBMITTED = "SUBMITTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    PROTECTION_PENDING = "PROTECTION_PENDING"
    PROTECTED = "PROTECTED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
    CLOSED = "CLOSED"


class BlockCode(StrEnum):
    DATA_NOT_CLOSED = "DATA_NOT_CLOSED"
    DATA_STALE = "DATA_STALE"
    DATA_GAP = "DATA_GAP"
    DATA_MISALIGNED = "DATA_MISALIGNED"
    DUPLICATE_DECISION = "DUPLICATE_DECISION"
    CONFIG_CONFLICT = "CONFIG_CONFLICT"
    MARKET_RULES_UNKNOWN = "MARKET_RULES_UNKNOWN"
    ORDER_BELOW_MINIMUM = "ORDER_BELOW_MINIMUM"
    PRICE_DEVIATION_EXCEEDED = "PRICE_DEVIATION_EXCEEDED"
    SPREAD_EXCEEDED = "SPREAD_EXCEEDED"
    NET_RR_INSUFFICIENT = "NET_RR_INSUFFICIENT"
    CORRELATION_DIRECTION_CONFLICT = "CORRELATION_DIRECTION_CONFLICT"
    RISK_LIMIT_EXCEEDED = "RISK_LIMIT_EXCEEDED"
    UNPROTECTED_POSITION = "UNPROTECTED_POSITION"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
    EXTERNAL_ORDER_PRESENT = "EXTERNAL_ORDER_PRESENT"


class DecisionEventType(StrEnum):
    DATA_VALIDATED = "DATA_VALIDATED"
    STRATEGY_SIGNAL = "STRATEGY_SIGNAL"
    PORTFOLIO_DECISION = "PORTFOLIO_DECISION"
    RISK_DECISION = "RISK_DECISION"
    TRADE_INTENT_CREATED = "TRADE_INTENT_CREATED"
    ORDER_NORMALIZED = "ORDER_NORMALIZED"
    ORDER_SUBMITTED = "ORDER_SUBMITTED"
    ORDER_UPDATE = "ORDER_UPDATE"
    PROTECTION_CREATED = "PROTECTION_CREATED"
    RECONCILED = "RECONCILED"
    POSITION_CLOSED = "POSITION_CLOSED"
    BLOCKED = "BLOCKED"


class ValidatedMarketSnapshot(ImmutableContract):
    symbol: str
    bid_price: Decimal = Field(gt=0)
    ask_price: Decimal = Field(gt=0)
    mark_price: Decimal = Field(gt=0)
    exchange_time: datetime
    received_at: datetime
    source_event_time: datetime | None = None

    @model_validator(mode="after")
    def validate_spread(self) -> ValidatedMarketSnapshot:
        if self.ask_price < self.bid_price:
            raise ValueError("ask_price must be greater than or equal to bid_price")
        return self


class ValidatedCandle(ImmutableContract):
    open_time: datetime
    close_time: datetime
    open_price: Decimal = Field(gt=0)
    high_price: Decimal = Field(gt=0)
    low_price: Decimal = Field(gt=0)
    close_price: Decimal = Field(gt=0)
    volume: Decimal = Field(ge=0)
    received_at: datetime
    source_event_time: datetime | None = None
    closed: bool
    close_proof: str


class ValidatedCandleSet(ImmutableContract):
    symbol: str
    timeframe: str
    candles: tuple[ValidatedCandle, ...]
    validated_at: datetime
    exchange_server_time: datetime
    aligned_close_time: datetime


class StrategySignal(ImmutableContract):
    decision_id: str
    symbol: str
    side: PositionSide
    score: Decimal = Field(ge=0, le=100)
    score_components: dict[str, Decimal] = Field(default_factory=dict)
    regime: MarketRegime
    signal_candle_close_time: datetime
    strategy_id: str
    strategy_version: str


class PortfolioDecision(ImmutableContract):
    decision_id: str
    symbol: str
    raw_side: PositionSide
    final_side: PositionSide
    accepted: bool
    block_codes: tuple[BlockCode, ...] = ()
    reason: str | None = None


class RiskDecision(ImmutableContract):
    decision_id: str
    accepted: bool
    requested_notional: Decimal = Field(ge=0)
    portfolio_exposure_after: Decimal = Field(ge=0)
    initial_risk_fraction_after: Decimal = Field(ge=0)
    actual_reward_risk: Decimal | None = Field(default=None, ge=0)
    block_codes: tuple[BlockCode, ...] = ()


class ProtectionPolicy(ImmutableContract):
    stop_price: Decimal = Field(gt=0)
    take_profit_price: Decimal | None = Field(default=None, gt=0)
    working_type: str = "MARK_PRICE"


class TradeIntent(ImmutableContract):
    intent_id: str
    cycle_id: str
    decision_id: str
    strategy_id: str
    strategy_version: str
    config_snapshot_id: str
    config_hash: str
    runtime_mode: RuntimeMode
    symbol: str
    action: TradeAction
    position_side: PositionSide
    exchange_side: ExchangeSide
    target_quantity: Decimal = Field(gt=0)
    signal_reference_price: Decimal = Field(gt=0)
    protection: ProtectionPolicy
    signal_candle_close_time: datetime
    created_at: datetime


class MarketRulesSnapshot(ImmutableContract):
    rules_snapshot_id: str
    symbol: str
    market_status: str
    position_mode: str
    margin_mode: str
    leverage: Decimal = Field(ge=1)
    tick_size: Decimal = Field(gt=0)
    step_size: Decimal = Field(gt=0)
    min_quantity: Decimal = Field(gt=0)
    max_quantity: Decimal | None = Field(default=None, gt=0)
    min_notional: Decimal = Field(gt=0)
    max_notional: Decimal | None = Field(default=None, gt=0)
    loaded_at: datetime
    source: str = "exchange_market_metadata"


class NormalizedOrder(ImmutableContract):
    intent_id: str
    client_order_id: str
    symbol: str
    side: ExchangeSide
    position_side: str
    reduce_only: bool | None = None
    close_position: bool = False
    quantity: Decimal = Field(gt=0)
    price: Decimal | None = Field(default=None, gt=0)
    stop_price: Decimal | None = Field(default=None, gt=0)
    working_type: str = "MARK_PRICE"
    time_in_force: str = "GTC"
    rules_snapshot: MarketRulesSnapshot


class ExecutionReport(ImmutableContract):
    intent_id: str
    client_order_id: str
    exchange_order_id: str | None = None
    state: ExecutionState
    requested_quantity: Decimal = Field(gt=0)
    filled_quantity: Decimal = Field(ge=0)
    average_fill_price: Decimal | None = Field(default=None, gt=0)
    exchange_update_time: datetime | None = None
    received_at: datetime
    block_codes: tuple[BlockCode, ...] = ()


class DecisionEvent(ImmutableContract):
    event_id: str | None = None
    paper_run_id: str
    cycle_id: str
    decision_id: str
    event_type: DecisionEventType
    block_code: BlockCode | None = None
    strategy_id: str
    strategy_version: str
    config_snapshot_id: str
    config_hash: str
    symbol: str
    timeframe: str
    candle_close_time: datetime
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None


_SECRET_KEYS = {
    "api_key",
    "apikey",
    "secret",
    "secret_key",
    "private_key",
    "password",
    "passphrase",
    "token",
    "access_token",
    "refresh_token",
}


def _contains_secret_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, nested in value.items():
            if str(key).strip().lower() in _SECRET_KEYS or _contains_secret_key(nested):
                return True
    elif isinstance(value, list | tuple):
        return any(_contains_secret_key(item) for item in value)
    return False


def canonical_config_hash(config: dict[str, Any]) -> str:
    canonical = json.dumps(config, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


class ConfigSnapshot(ImmutableContract):
    config_snapshot_id: str | None = None
    paper_run_id: str
    config: dict[str, Any]
    config_hash: str
    created_by: str
    created_at: datetime | None = None
    effective_cycle_id: str
    previous_snapshot_id: str | None = None

    @model_validator(mode="after")
    def validate_snapshot(self) -> ConfigSnapshot:
        if _contains_secret_key(self.config):
            raise ValueError("secret-bearing configuration keys cannot be persisted")
        expected_hash = canonical_config_hash(self.config)
        if self.config_hash != expected_hash:
            raise ValueError("config_hash does not match canonical configuration")
        return self

    @classmethod
    def create(
        cls,
        *,
        paper_run_id: str,
        config: dict[str, Any],
        created_by: str,
        effective_cycle_id: str,
        previous_snapshot_id: str | None = None,
    ) -> ConfigSnapshot:
        return cls(
            paper_run_id=paper_run_id,
            config=config,
            config_hash=canonical_config_hash(config),
            created_by=created_by,
            effective_cycle_id=effective_cycle_id,
            previous_snapshot_id=previous_snapshot_id,
        )


class ConfigSnapshotCreateRequest(PlatformModel):
    config: dict[str, Any]
    base_config_hash: str | None = None
    created_by: str
    effective_cycle_id: str
