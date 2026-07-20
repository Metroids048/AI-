"""Deterministic strategy-to-intent boundary shared by replay, shadow and paper adapters."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal

from shared.models import (
    ExchangeSide,
    PortfolioDecision,
    ProtectionPolicy,
    RuntimeMode,
    StrategySignal,
    TradeAction,
    TradeIntent,
)


class DecisionEngine:
    def build_intent(
        self,
        *,
        adapter_mode: RuntimeMode,
        cycle_id: str,
        signal: StrategySignal,
        portfolio: PortfolioDecision,
        config_snapshot_id: str,
        config_hash: str,
        quantity: Decimal,
        reference_price: Decimal,
        protection: ProtectionPolicy,
        action: TradeAction,
        exchange_side: ExchangeSide,
    ) -> TradeIntent:
        if not portfolio.accepted or portfolio.final_side is not signal.side:
            raise ValueError("portfolio decision does not authorize the strategy signal")
        identity = "|".join(
            (
                cycle_id,
                signal.decision_id,
                config_hash,
                signal.symbol,
                action.value,
                signal.side.value,
                str(quantity),
            )
        )
        return TradeIntent(
            intent_id=f"intent:{hashlib.sha256(identity.encode('utf-8')).hexdigest()}",
            cycle_id=cycle_id,
            decision_id=signal.decision_id,
            strategy_id=signal.strategy_id,
            strategy_version=signal.strategy_version,
            config_snapshot_id=config_snapshot_id,
            config_hash=config_hash,
            runtime_mode=adapter_mode,
            symbol=signal.symbol,
            action=action,
            position_side=signal.side,
            exchange_side=exchange_side,
            target_quantity=quantity,
            signal_reference_price=reference_price,
            protection=protection,
            signal_candle_close_time=signal.signal_candle_close_time,
            created_at=signal.signal_candle_close_time,
        )

    @staticmethod
    def execution_intent_bytes(intent: TradeIntent) -> bytes:
        payload = intent.model_dump(mode="json", exclude={"runtime_mode"})
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
