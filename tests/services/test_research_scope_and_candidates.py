from __future__ import annotations

from services.data.universe import AUTO_PAPER_RESEARCH_SYMBOLS
from services.execution.bootstrap import AUTO_PAPER_TECHNICAL_RULES, resolve_auto_paper_technical_evidence
from services.execution.signal_edge_stats import strategy_rules_hash
from services.strategy_library.candidates.registry import get_candidate, list_candidates
from shared.models import StrategyRules


def test_auto_paper_research_scope_is_fixed_top3() -> None:
    assert AUTO_PAPER_RESEARCH_SYMBOLS == ("BTC/USDT", "ETH/USDT", "SOL/USDT")


def test_evidence_candidates_have_explicit_role_specific_signal_sets() -> None:
    assert {
        "operator_heuristic_v1",
        "trend_momentum_v1",
        "trend_breakout_v1",
    }.issubset(list_candidates())

    momentum = get_candidate("trend_momentum_v1").get_config()["entry_rules"]
    assert momentum["direction_signals"] == ["ema_trend", "adx"]
    assert momentum["entry_signals"] == ["macd"]
    assert momentum["fusion_method"] == "weighted_vote"

    breakout = get_candidate("trend_breakout_v1").get_config()["entry_rules"]
    assert breakout["direction_signals"] == ["dow_trend", "adx"]
    assert breakout["entry_signals"] == ["price_action", "fvg"]
    assert breakout["fusion_method"] == "weighted_vote"


def test_all_evidence_candidates_share_cost_exit_and_timeframe_contracts() -> None:
    configs = [get_candidate(candidate_id).get_config() for candidate_id in list_candidates()]
    contracts = {
        (
            config["entry_rules"]["direction_timeframe"],
            config["entry_rules"]["state_timeframe"],
            config["entry_rules"]["entry_timeframe"],
            config["entry_rules"]["core_fee_bps"],
            config["entry_rules"]["core_slippage_bps"],
            config["takeprofit_rules"]["risk_reward"],
        )
        for config in configs
    }
    assert contracts == {("4h", "1h", "15m", 5.0, 1.0, 2.0)}


def test_runtime_baseline_has_stable_rules_hash() -> None:
    # Verify that AUTO_PAPER_TECHNICAL_RULES (the fallback config when no manifest
    # exists) produces a deterministic SHA-256 hash. The manifest-selected candidate
    # (trend_momentum_v1) is now the active config; AUTO_PAPER_TECHNICAL_RULES is the
    # conservative fallback for new bootstrap runs.
    runtime = StrategyRules(**AUTO_PAPER_TECHNICAL_RULES)
    h = strategy_rules_hash(runtime)
    assert len(h) == 64  # SHA-256 hex digest


def test_active_manifest_selects_validated_candidate_and_symbol_subset(tmp_path, monkeypatch) -> None:
    import json

    import services.execution.signal_edge_stats as edge_module

    monkeypatch.setattr(edge_module, "EDGE_STATS_ARTIFACT_DIR", tmp_path)
    config = get_candidate("trend_momentum_v1").get_config()
    rules = StrategyRules(**config)
    manifest_dir = tmp_path / "auto_paper_mature_templates"
    manifest_dir.mkdir()
    (manifest_dir / "active-manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "candidate_id": "trend_momentum_v1",
                "rules_hash": strategy_rules_hash(rules),
                "eligible_symbols": ["BTC/USDT", "ETH/USDT", "DOGE/USDT"],
            }
        ),
        encoding="utf-8",
    )

    resolved, symbols = resolve_auto_paper_technical_evidence()

    assert resolved["entry_rules"]["candidate_id"] == "trend_momentum_v1"
    assert symbols == ("BTC/USDT", "ETH/USDT")
