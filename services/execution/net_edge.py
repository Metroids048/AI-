"""Net expectancy after fees/slippage for execution admission."""

from __future__ import annotations

from statistics import mean
from typing import Any

from services.data.universe import execution_scope_hash
from shared.config import settings


def meta_label_edge_stats(net_returns: list[float]) -> dict[str, float]:
    """Derive win-rate and average win/loss magnitudes from historical sample returns."""
    if not net_returns:
        return {
            "sample_count": 0.0,
            "win_rate": 0.0,
            "average_win": 0.0,
            "average_loss": 0.0,
        }
    wins = [value for value in net_returns if value > 0]
    losses = [abs(value) for value in net_returns if value < 0]
    return {
        "sample_count": float(len(net_returns)),
        "win_rate": len(wins) / len(net_returns),
        "average_win": mean(wins) if wins else 0.0,
        "average_loss": mean(losses) if losses else 0.0,
    }


def net_edge_after_cost(
    *,
    win_rate: float,
    average_win: float,
    average_loss: float,
    round_trip_fee_rate: float,
    round_trip_slippage_rate: float,
) -> float:
    """Gross expectancy minus round-trip fee and slippage rates (all fractional returns)."""
    return win_rate * average_win - (1.0 - win_rate) * average_loss - round_trip_fee_rate - round_trip_slippage_rate


def net_edge_rejection_codes(entry_context: dict[str, Any]) -> list[str]:
    """Return gatekeeper rejection codes for non-positive post-cost expectancy."""
    if bool(entry_context.get("close_only_mode", False)):
        return []
    if bool(entry_context.get("observation_only_mode", False)):
        return []  # Signal observation channel: bypass cost gate, other risk controls remain active
    if bool(entry_context.get("testnet_sampling_mode", False)) and (
        entry_context.get("decision_variant") == "simulation_sampling_fallback"
        and entry_context.get("strategy_lane") == "directional"
        and entry_context.get("execution_scope_hash") == execution_scope_hash()
        and settings.binance_use_testnet
        and settings.binance_auto_execute
        and not settings.live_trading_enabled
    ):
        # The sampling fallback exists to collect real Testnet fills before it
        # has deployable OOS evidence.  Only this pre-validation expectancy gate
        # is bypassed; Gatekeeper stop, position, leverage, exposure, loss and
        # kill-switch checks still run unchanged.
        return []
    if bool(entry_context.get("validated_edge_required", False)):
        validated_edge = entry_context.get("validated_edge_net_expectancy")
        if validated_edge is None:
            return ["validated_edge_stats_missing_or_stale"]
        entry_context["estimated_net_edge_after_cost"] = float(validated_edge)
        if float(validated_edge) <= 0:
            return ["net_edge_after_cost_negative"]
        return []
    required = (
        "meta_label_win_rate",
        "meta_label_average_win",
        "meta_label_average_loss",
        "round_trip_fee_rate",
        "round_trip_slippage_rate",
    )
    if any(entry_context.get(key) is None for key in required):
        # Non-technical / incomplete contexts skip this gate; paper technical path always fills it.
        if entry_context.get("decision_pipeline") is None:
            return []
        return ["missing_meta_edge_stats"]
    edge = net_edge_after_cost(
        win_rate=float(entry_context["meta_label_win_rate"]),
        average_win=float(entry_context["meta_label_average_win"]),
        average_loss=float(entry_context["meta_label_average_loss"]),
        round_trip_fee_rate=float(entry_context["round_trip_fee_rate"]),
        round_trip_slippage_rate=float(entry_context["round_trip_slippage_rate"]),
    )
    entry_context["estimated_net_edge_after_cost"] = edge
    if edge <= 0:
        return ["net_edge_after_cost_negative"]
    return []
