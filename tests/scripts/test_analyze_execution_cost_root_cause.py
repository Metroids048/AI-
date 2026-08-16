import json
from decimal import Decimal

from scripts.analyze_execution_cost_root_cause import analyze


def test_selects_atr_native_filter_when_cost_rises_as_risk_band_narrows(tmp_path) -> None:
    decomposition = tmp_path / "decomposition.json"
    lineage = tmp_path / "lineage.json"
    decomposition.write_text(
        json.dumps(
            [
                {
                    "position_id": "a",
                    "symbol": "BTC/USDT",
                    "direction": "long",
                    "exit_reason": "HARD_STOP",
                    "holding_minutes": 10,
                    "entry_fee_usdt": "0.4",
                    "exit_fee_usdt": "0.4",
                    "risk_usdt": "3.5",
                    "cost_r": "0.2285714286",
                    "net_r": "-1.2",
                },
                {
                    "position_id": "b",
                    "symbol": "ETH/USDT",
                    "direction": "short",
                    "exit_reason": "TAKE_PROFIT",
                    "holding_minutes": 20,
                    "entry_fee_usdt": "0.4",
                    "exit_fee_usdt": "0.4",
                    "risk_usdt": "7",
                    "cost_r": "0.1142857143",
                    "net_r": "1.1",
                },
            ]
        ),
        encoding="utf-8",
    )
    lineage.write_text(
        json.dumps(
            {
                "status": "READ_ONLY",
                "waterfall_total_r": {"unknown_residual": "0", "abnormal_exits": "0"},
                "episodes": [
                    {
                        "position_id": "a",
                        "entry": {"fills": [{"raw_hash": "ae", "filled_quantity": "1", "fill_price": "1000"}]},
                        "exit": {"fills": [{"raw_hash": "ax", "filled_quantity": "1", "fill_price": "1000"}]},
                        "waterfall_r": {"trigger_to_fill_slippage": "-0.2"},
                    },
                    {
                        "position_id": "b",
                        "entry": {"fills": [{"raw_hash": "be", "filled_quantity": "1", "fill_price": "1000"}]},
                        "exit": {"fills": [{"raw_hash": "bx", "filled_quantity": "1", "fill_price": "1000"}]},
                        "waterfall_r": {"trigger_to_fill_slippage": "-0.1"},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    report = analyze(decomposition_path=decomposition, lineage_path=lineage)

    assert report["selected_single_variable"] == "ATR_NATIVE_ONLY_FILTER"
    assert Decimal(report["fee_rate_consistency"]["observed_entry_fee_rate_mean"]) == Decimal("0.0004")
    assert report["groups"]["exit_reason"]["STOP"]["trades"] == 1
