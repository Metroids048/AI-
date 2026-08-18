"""Safety contracts for the operator-authorized Testnet sizing script."""

from types import SimpleNamespace

import pytest

from scripts import apply_e010_testnet_sizing_profile as sizing_script


def test_pending_snapshot_is_never_silently_replaced() -> None:
    """Concurrent NEXT_CYCLE work must survive a sizing-profile invocation."""
    pending = SimpleNamespace(config_snapshot_id="operator-pending-snapshot")
    snapshots = SimpleNamespace(get_pending=lambda _: pending)

    with pytest.raises(RuntimeError, match="operator-pending-snapshot"):
        sizing_script._require_no_pending_snapshot(snapshots, "paper-run")


def test_apply_targets_stages_the_five_symbol_canary_contract() -> None:
    profile = {
        "asset_risk_tiers": {
            "core": {
                "symbols": list(sizing_script.EXECUTION_SYMBOLS),
                "leverage": 50,
                "max_position_fraction": 2.5,
            }
        }
    }
    updated = sizing_script._apply_targets(
        profile,
        leverage=sizing_script.TARGET_MAX_LEVERAGE,
        margin=sizing_script.TARGET_MAX_MARGIN_FRACTION,
    )
    assert updated["risk_per_trade"] == 0.10
    assert updated["max_leverage"] == 30.0
    assert updated["max_margin_fraction"] == 0.05
    assert updated["max_symbol_exposure"] == 1.50
    assert updated["max_total_exposure"] == 7.50
    assert updated["max_open_positions"] == 5
    assert updated["simulation_sampling_fallback_enabled"] is True
    assert tuple(sizing_script.EXECUTION_SYMBOLS) == (
        "BTC/USDT",
        "ETH/USDT",
        "SOL/USDT",
        "XRP/USDT",
        "BNB/USDT",
    )
