from __future__ import annotations

import csv
from pathlib import Path

import pandas as pd
import pytest

from research_source.worldquant_adapter import (
    CryptoFactorGenerator,
    LocalAlphaScanner,
    UnsupportedAlphaExpression,
    evaluate_alpha_expression,
    parse_alpha_expression,
)
from research_source.worldquant_adapter.local_alpha_scanner import SUPPORTED_FILES


def _frame() -> pd.DataFrame:
    index = pd.RangeIndex(start=0, stop=40, step=1)
    base = pd.Series(range(40), index=index, dtype="float64")
    return pd.DataFrame(
        {
            "open": 100 + base,
            "high": 101 + base,
            "low": 99 + base,
            "close": 100.5 + base,
            "volume": 1_000 + (base * 10),
            "vwap": 100.2 + base,
            "funding_rate": ((base % 5) - 2) * 0.0001,
            "open_interest": 40_000 + (base * 100),
            "long_ratio": 0.45 + ((base % 4) * 0.05),
            "short_ratio": 0.55 - ((base % 4) * 0.05),
            "liquidation_usd": 100 + (base * 3),
        },
        index=index,
    )


def test_rank_grouping_changes_expression_meaning() -> None:
    frame = _frame()
    diff_of_ranks = evaluate_alpha_expression("rank(close)-rank(volume)", frame)
    rank_of_diff = evaluate_alpha_expression("rank(close-volume)", frame)
    assert not diff_of_ranks.equals(rank_of_diff)


def test_nested_supported_expression_computes() -> None:
    frame = _frame()
    result = evaluate_alpha_expression("group_neutralize(ts_zscore(close, 5), industry)", frame)
    assert len(result) == len(frame)
    assert result.dtype == "float64"


@pytest.mark.parametrize(
    ("expr", "message_fragment"),
    [
        ("ts_delta(capex_to_total_assets, 252)", "capex_to_total_assets"),
        ("signed_power(close, 2)", "signed_power"),
    ],
)
def test_unsupported_inputs_and_operators_fail_loudly(expr: str, message_fragment: str) -> None:
    plan = parse_alpha_expression(expr)
    assert plan.evaluable is False
    with pytest.raises(UnsupportedAlphaExpression, match=message_fragment):
        evaluate_alpha_expression(expr, _frame())


def test_group_alias_mapping_is_explicit() -> None:
    plan = parse_alpha_expression(
        "group_neutralize(close, industry)"
        "+group_rank(close, sector)"
        "+group_rank(close, subindustry)"
        "+group_rank(close, market)"
    )
    assert plan.group_aliases == {
        "industry": "volatility_regime",
        "sector": "funding_regime",
        "subindustry": "liquidity_regime",
        "market": "market",
    }


def test_crypto_factor_generator_delegates_to_evaluator() -> None:
    generator = CryptoFactorGenerator()
    code = generator.from_alpha_plan(parse_alpha_expression("ts_rank(close, 5)"))
    namespace: dict[str, object] = {}
    exec(code, namespace)
    signal = namespace["compute_factor"](_frame())
    assert len(signal) == 40
    assert signal.name == "ported_alpha_signal"


def test_local_alpha_scanner_preserves_supported_and_rejected_metadata(tmp_path: Path) -> None:
    alpha_root = tmp_path / "alpha"
    alpha_root.mkdir()
    path = alpha_root / SUPPORTED_FILES[-1]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "alpha_id",
                "expression",
                "profile",
                "sharpe",
                "fitness",
                "turnover",
                "returns",
                "drawdown",
                "failure_reasons",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "alpha_id": "A1",
                "expression": "ts_rank(close, 5)-rank(volume)",
                "profile": "momentum",
                "sharpe": "1.8",
                "fitness": "1.2",
                "turnover": "0.2",
                "returns": "0.1",
                "drawdown": "0.05",
                "failure_reasons": "",
            }
        )
        writer.writerow(
            {
                "alpha_id": "A2",
                "expression": "(group_rank(ts_delta(capex_to_total_assets, 252)/cap, industry)-0.5)*0.70",
                "profile": "fundamental",
                "sharpe": "2.5",
                "fitness": "1.5",
                "turnover": "0.3",
                "returns": "0.12",
                "drawdown": "0.04",
                "failure_reasons": "self_correlation_pending",
            }
        )

    ideas = LocalAlphaScanner().scan(alpha_root, limit=10)

    assert len(ideas) == 2
    assert ideas[0].intake_bucket == "rule_candidate"
    assert '"behavior_signature"' in (ideas[0].rationale or "")
    assert ideas[0].intake_metadata["raw_expression"] == "ts_rank(close, 5)-rank(volume)"
    assert ideas[0].intake_metadata["evaluable"] is True
    assert ideas[1].intake_bucket == "subjective_to_drop"
    assert "capex_to_total_assets" in (ideas[1].rationale or "")
    assert "capex_to_total_assets" in ideas[1].intake_metadata["unsupported_inputs"]
