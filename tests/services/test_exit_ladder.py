from services.execution.exit_ladder import (
    apply_level_fill,
    close_quantity_for_level,
    initialize_exit_ladder,
    level_hit,
    level_trigger_price,
    next_pending_level,
)
from shared.models import TradeSide


def test_initialize_exit_ladder_defaults() -> None:
    state = initialize_exit_ladder(
        symbol="BTC/USDT",
        side=TradeSide.LONG,
        entry_price=100.0,
        quantity=1.0,
        stop_price=95.0,
        takeprofit_rules={
            "exit_ladder": [
                {"r_multiple": 1.0, "close_fraction": 0.4},
                {"r_multiple": 1.5, "close_fraction": 0.3},
            ],
            "remainder_trail_after_r": 2.5,
        },
    )
    assert state is not None
    assert state.risk_distance == 5.0
    assert not state.all_levels_executed
    assert level_trigger_price(state, state.levels[0]) == 105.0
    assert level_trigger_price(state, state.levels[1]) == 107.5


def test_exit_ladder_level1_moves_stop_to_breakeven() -> None:
    state = initialize_exit_ladder(
        symbol="BTC/USDT",
        side=TradeSide.LONG,
        entry_price=100.0,
        quantity=1.0,
        stop_price=95.0,
        takeprofit_rules={
            "exit_ladder": [
                {"r_multiple": 1.0, "close_fraction": 0.4},
                {"r_multiple": 1.5, "close_fraction": 0.3},
            ],
            "remainder_trail_after_r": 2.5,
        },
    )
    assert state is not None
    level = next_pending_level(state)
    assert level is not None
    assert level_hit(state=state, level=level, bar_high=105.0, bar_low=99.0)
    qty = close_quantity_for_level(state, level)
    assert qty == 0.4
    updated = apply_level_fill(state, level=level, trigger_price=105.0, closed_quantity=qty)
    assert updated.remaining_quantity == 0.6
    assert updated.current_stop_price == 100.0
    assert updated.locked_level1_price == 105.0
    assert updated.levels[0].executed


def test_exit_ladder_level2_locks_stop_at_level1_price() -> None:
    state = initialize_exit_ladder(
        symbol="BTC/USDT",
        side=TradeSide.LONG,
        entry_price=100.0,
        quantity=1.0,
        stop_price=95.0,
        takeprofit_rules={
            "exit_ladder": [
                {"r_multiple": 1.0, "close_fraction": 0.4},
                {"r_multiple": 1.5, "close_fraction": 0.3},
            ],
            "remainder_trail_after_r": 2.5,
        },
    )
    assert state is not None
    l1 = next_pending_level(state)
    assert l1 is not None
    state = apply_level_fill(state, level=l1, trigger_price=105.0, closed_quantity=0.4)
    l2 = next_pending_level(state)
    assert l2 is not None
    assert level_hit(state=state, level=l2, bar_high=108.0, bar_low=104.0)
    qty = close_quantity_for_level(state, l2)
    assert abs(qty - 0.3) < 1e-9
    updated = apply_level_fill(state, level=l2, trigger_price=107.5, closed_quantity=qty)
    assert abs(updated.remaining_quantity - 0.3) < 1e-9
    assert updated.current_stop_price == 105.0
    assert updated.all_levels_executed
