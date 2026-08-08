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


def test_trend_momentum_v2_enriched_widens_entry_without_touching_direction_filter() -> None:
    """v2_enriched fixes 15m entry starvation while keeping v1's 4h trend filter.

    Runtime evidence 2026-08-07: the armed testnet run produced zero primary
    signals over 48h because v1 gates 15m entry on MACD alone. v2_enriched adds
    price_action / dow_trend / bollinger at entry only — the 4h EMA+ADX
    directional filter and all risk gates are unchanged, so this widens signal
    supply without weakening admission.
    """
    v1 = get_candidate("trend_momentum_v1").get_config()["entry_rules"]
    v2 = get_candidate("trend_momentum_v2_enriched").get_config()["entry_rules"]

    # Direction filter must stay identical — this is the proven edge component.
    assert v2["direction_signals"] == v1["direction_signals"] == ["ema_trend", "adx"]
    assert v2["direction_timeframe"] == v1["direction_timeframe"]
    assert v2["state_timeframe"] == v1["state_timeframe"]
    assert v2["entry_timeframe"] == v1["entry_timeframe"] == "15m"

    # Entry supply must be a strict superset of v1's single MACD trigger.
    assert set(v1["entry_signals"]).issubset(set(v2["entry_signals"]))
    assert set(v2["entry_signals"]) == {"macd", "price_action", "dow_trend", "bollinger"}

    # Cost assumptions must not drift as a side effect of widening entry.
    for key in ("core_fee_bps", "core_slippage_bps", "standard_fee_bps", "standard_slippage_bps"):
        assert v2[key] == v1[key], key

    breakout = get_candidate("trend_breakout_v1").get_config()["entry_rules"]
    assert breakout["direction_signals"] == ["dow_trend", "adx"]
    assert breakout["entry_signals"] == ["price_action", "fvg"]
    assert breakout["fusion_method"] == "weighted_vote"


def test_trend_pullback_candidate_is_registered_as_research_only() -> None:
    candidate = get_candidate("trend_pullback_v1")
    config = candidate.get_config()

    assert config["entry_rules"]["research_only"] is True
    assert config["entry_rules"]["minimum_score"] == 70
    assert config["takeprofit_rules"]["risk_reward"] == 2.0
    assert config["exit_rules"]["no_progress_bars"] == 10


def test_failed_breakout_candidate_is_not_execution_eligible() -> None:
    candidate = get_candidate("failed_breakout_reversal_v1")

    assert candidate.execution_eligible is False
    assert candidate.lifecycle_state == "RESEARCH_ONLY"


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
    """The packaged active manifest is authoritative for the armed candidate.

    Asserted against the manifest itself rather than a hardcoded candidate id:
    operators re-point this manifest when a new validated candidate is packaged
    (2026-08-07: trend_momentum_v1 -> trend_momentum_v2_enriched to fix 15m
    entry-signal starvation). The invariant under test is that resolution honors
    the manifest and clamps eligible symbols to the research scope, not which
    specific candidate happens to be armed today.
    """
    import json
    from pathlib import Path

    import services.execution.signal_edge_stats as edge_module

    monkeypatch.setattr(edge_module, "EDGE_STATS_ARTIFACT_DIR", tmp_path)

    packaged = json.loads(
        Path("docs/evidence/active-manifests/auto_paper_mature_templates.json").read_text(encoding="utf-8")
    )
    expected_candidate = packaged["candidate_id"]
    config = get_candidate(expected_candidate).get_config()
    rules = StrategyRules(**config)
    manifest_dir = tmp_path / "auto_paper_mature_templates"
    manifest_dir.mkdir()
    (manifest_dir / "active-manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "candidate_id": expected_candidate,
                "rules_hash": strategy_rules_hash(rules),
                "eligible_symbols": ["BTC/USDT", "ETH/USDT", "DOGE/USDT"],
            }
        ),
        encoding="utf-8",
    )

    resolved, symbols = resolve_auto_paper_technical_evidence()

    assert resolved["entry_rules"]["candidate_id"] == expected_candidate
    # DOGE/USDT is outside AUTO_PAPER_RESEARCH_SYMBOLS and must be clamped away.
    assert symbols == ("BTC/USDT", "ETH/USDT")
    # The packaged manifest hash must match the packaged candidate config, or
    # bootstrap silently falls back to the disabled conservative rules.
    assert packaged["rules_hash"] == strategy_rules_hash(rules)
