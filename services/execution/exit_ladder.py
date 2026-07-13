"""Multi-level exit ladder: partial take-profit, break-even, then trailing remainder."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from shared.models import TradeSide


@dataclass(frozen=True)
class LadderLevel:
    r_multiple: float
    close_fraction: float
    executed: bool = False
    trigger_price: float | None = None


@dataclass(frozen=True)
class ExitLadderState:
    symbol: str
    side: str
    entry_price: float
    original_quantity: float
    remaining_quantity: float
    initial_stop_price: float
    current_stop_price: float
    levels: tuple[LadderLevel, ...]
    remainder_trail_after_r: float
    locked_level1_price: float | None = None

    @property
    def risk_distance(self) -> float:
        return abs(self.entry_price - self.initial_stop_price)

    @property
    def all_levels_executed(self) -> bool:
        return all(level.executed for level in self.levels)

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "entry_price": self.entry_price,
            "original_quantity": self.original_quantity,
            "remaining_quantity": self.remaining_quantity,
            "initial_stop_price": self.initial_stop_price,
            "current_stop_price": self.current_stop_price,
            "remainder_trail_after_r": self.remainder_trail_after_r,
            "locked_level1_price": self.locked_level1_price,
            "levels": [
                {
                    "r_multiple": level.r_multiple,
                    "close_fraction": level.close_fraction,
                    "executed": level.executed,
                    "trigger_price": level.trigger_price,
                }
                for level in self.levels
            ],
        }


def ladder_config_from_rules(takeprofit_rules: dict[str, Any]) -> list[dict[str, float]] | None:
    raw = takeprofit_rules.get("exit_ladder")
    if not isinstance(raw, list) or not raw:
        return None
    levels: list[dict[str, float]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        r_multiple = float(item["r_multiple"])
        close_fraction = float(item["close_fraction"])
        if r_multiple <= 0 or not 0 < close_fraction < 1:
            continue
        levels.append({"r_multiple": r_multiple, "close_fraction": close_fraction})
    return levels or None


def initialize_exit_ladder(
    *,
    symbol: str,
    side: TradeSide,
    entry_price: float,
    quantity: float,
    stop_price: float,
    takeprofit_rules: dict[str, Any],
) -> ExitLadderState | None:
    levels_cfg = ladder_config_from_rules(takeprofit_rules)
    if levels_cfg is None:
        return None
    risk = abs(entry_price - stop_price)
    if risk <= 0 or quantity == 0:
        return None
    remainder_trail = float(takeprofit_rules.get("remainder_trail_after_r", 2.5))
    levels = tuple(
        LadderLevel(r_multiple=item["r_multiple"], close_fraction=item["close_fraction"]) for item in levels_cfg
    )
    return ExitLadderState(
        symbol=symbol,
        side=side.value if hasattr(side, "value") else str(side),
        entry_price=entry_price,
        original_quantity=abs(quantity),
        remaining_quantity=abs(quantity),
        initial_stop_price=stop_price,
        current_stop_price=stop_price,
        levels=levels,
        remainder_trail_after_r=remainder_trail,
    )


def exit_ladder_from_dict(payload: dict[str, Any]) -> ExitLadderState:
    levels = tuple(
        LadderLevel(
            r_multiple=float(item["r_multiple"]),
            close_fraction=float(item["close_fraction"]),
            executed=bool(item.get("executed", False)),
            trigger_price=item.get("trigger_price"),
        )
        for item in payload.get("levels", [])
    )
    return ExitLadderState(
        symbol=str(payload["symbol"]),
        side=str(payload["side"]),
        entry_price=float(payload["entry_price"]),
        original_quantity=float(payload["original_quantity"]),
        remaining_quantity=float(payload["remaining_quantity"]),
        initial_stop_price=float(payload["initial_stop_price"]),
        current_stop_price=float(payload["current_stop_price"]),
        levels=levels,
        remainder_trail_after_r=float(payload.get("remainder_trail_after_r", 2.5)),
        locked_level1_price=payload.get("locked_level1_price"),
    )


def next_pending_level(state: ExitLadderState) -> LadderLevel | None:
    for level in state.levels:
        if not level.executed:
            return level
    return None


def level_trigger_price(state: ExitLadderState, level: LadderLevel) -> float:
    distance = state.risk_distance * level.r_multiple
    if state.side == TradeSide.LONG.value:
        return state.entry_price + distance
    return state.entry_price - distance


def level_hit(*, state: ExitLadderState, level: LadderLevel, bar_high: float, bar_low: float) -> bool:
    price = level_trigger_price(state, level)
    if state.side == TradeSide.LONG.value:
        return bar_high >= price
    return bar_low <= price


def close_quantity_for_level(state: ExitLadderState, level: LadderLevel) -> float:
    return min(state.remaining_quantity, state.original_quantity * level.close_fraction)


def apply_level_fill(
    state: ExitLadderState,
    *,
    level: LadderLevel,
    trigger_price: float,
    closed_quantity: float,
) -> ExitLadderState:
    updated_levels: list[LadderLevel] = []
    level_index = -1
    for index, existing in enumerate(state.levels):
        if (
            level_index < 0
            and existing.r_multiple == level.r_multiple
            and existing.close_fraction == level.close_fraction
            and not existing.executed
        ):
            updated_levels.append(
                LadderLevel(
                    r_multiple=existing.r_multiple,
                    close_fraction=existing.close_fraction,
                    executed=True,
                    trigger_price=trigger_price,
                )
            )
            level_index = index
        else:
            updated_levels.append(existing)
    if level_index < 0:
        raise ValueError("exit ladder level not pending")
    remaining = max(0.0, state.remaining_quantity - closed_quantity)
    # Spec: L1 → stop to entry (BE); L2+ → stop to L1 trigger price.
    if level_index == 0:
        next_stop = state.entry_price
        locked: float | None = trigger_price
    else:
        first = updated_levels[0]
        next_stop = first.trigger_price if first.trigger_price is not None else state.entry_price
        locked = first.trigger_price
    return ExitLadderState(
        symbol=state.symbol,
        side=state.side,
        entry_price=state.entry_price,
        original_quantity=state.original_quantity,
        remaining_quantity=remaining,
        initial_stop_price=state.initial_stop_price,
        current_stop_price=next_stop,
        levels=tuple(updated_levels),
        remainder_trail_after_r=state.remainder_trail_after_r,
        locked_level1_price=locked,
    )


def next_trailed_stop_price(
    *,
    side: str,
    entry_price: float,
    current_stop_price: float,
    initial_distance: float,
    trail_after_r: float,
    bar_high: float,
    bar_low: float,
) -> float | None:
    """Ratchet a stop to breakeven once price has moved trail_after_r*initial_distance
    in the position's favor. Returns the new stop price, or None if the trail hasn't
    triggered yet or would not improve the current stop.

    Shared by PaperRuntimeService._apply_trailing_ratchet (fixed-stop strategies, and
    exit-ladder remainders once all partial levels are filled) and the technical
    validation replay engine's exit-ladder remainder handling, so both production and
    backtest paths ratchet on identical math.
    """

    if initial_distance <= 0:
        return None
    if side == TradeSide.LONG.value:
        favorable_move = bar_high - entry_price
        if favorable_move < trail_after_r * initial_distance:
            return None
        next_stop = max(current_stop_price, entry_price)
        return next_stop if next_stop > current_stop_price else None
    favorable_move = entry_price - bar_low
    if favorable_move < trail_after_r * initial_distance:
        return None
    next_stop = min(current_stop_price, entry_price)
    return next_stop if next_stop < current_stop_price else None


def store_exit_ladder(metrics: dict[str, Any], state: ExitLadderState) -> dict[str, Any]:
    ladders = dict(metrics.get("exit_ladder", {}))
    ladders[state.symbol] = state.as_dict()
    updated = deepcopy(metrics)
    updated["exit_ladder"] = ladders
    return updated


def load_exit_ladder(metrics: dict[str, Any], symbol: str) -> ExitLadderState | None:
    ladders = metrics.get("exit_ladder", {})
    if not isinstance(ladders, dict):
        return None
    payload = ladders.get(symbol)
    if not isinstance(payload, dict):
        return None
    return exit_ladder_from_dict(payload)
