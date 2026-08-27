from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from services.research.quant_knowledge import (
    HypothesisRegistry,
    QuantPrimitive,
    QuantPrimitiveRegistry,
    ResearchHypothesis,
    compose_research_candidate,
    export_quant_knowledge,
)
from services.research.quant_knowledge.runner import (
    _bootstrap_delta,
    _forward_distribution,
    _paired_bootstrap_delta,
    _rebuild_progress,
    _validate_experiment_design,
)
from services.strategy_library.event_edge import EdgeEvent, EventBar


def _primitive(**overrides: object) -> QuantPrimitive:
    payload: dict[str, Any] = {
        "primitive_id": "QP_TEST",
        "concept": "test",
        "role": "FILTER",
        "natural_language_thesis": "test thesis",
        "quantization_status": "PROXY_ALLOWED",
        "provenance": "PROXY_DERIVED",
    }
    payload.update(overrides)
    return QuantPrimitive.model_validate(payload)


def test_primitive_registry_rejects_same_id_drift() -> None:
    registry = QuantPrimitiveRegistry([_primitive()])
    with pytest.raises(ValueError, match="PRIMITIVE_ID_CONFLICT"):
        registry.register(_primitive(concept="changed"))


def test_hypothesis_registration_is_required_before_binding() -> None:
    hypothesis = ResearchHypothesis(
        hypothesis_id="HYP-1",
        claim="claim",
        base_event="HTF_BREAK_RETEST",
        primitive="QP_TEST",
        metric="net_expectancy",
    )
    with pytest.raises(ValueError, match="HYPOTHESIS_MUST_BE_REGISTERED"):
        hypothesis.to_experiment_spec(dataset_id="d", dataset_hash="dh", strategy_hash="sh")
    registered = HypothesisRegistry().register(hypothesis)
    spec = registered.to_experiment_spec(dataset_id="d", dataset_hash="dh", strategy_hash="sh")
    assert spec.engine_options["research_hypothesis_id"] == "HYP-1"
    assert spec.input_spec_hash


def test_exporter_emits_proxy_provenance_and_research_only_artifacts(tmp_path: Path) -> None:
    root = tmp_path / "qinxiongmao"
    (root / "agent_corpus").mkdir(parents=True)
    (root / "strategy_research").mkdir(parents=True)
    unit = {
        "unit_id": "KU-1",
        "video_id": "video-1",
        "content": "突破后回踩并站稳支撑，配合成交量确认",
        "topic": ["Support Resistance", "Breakout"],
        "timeframe": ["1h"],
        "market": ["趋势"],
        "clean_source_refs": ["cleaned.json#segments/0"],
    }
    (root / "agent_corpus" / "knowledge_units.jsonl").write_text(json.dumps(unit) + "\n", encoding="utf-8")
    (root / "strategy_research" / "rule_candidates.jsonl").write_text("", encoding="utf-8")
    output = tmp_path / "export"
    bundle = export_quant_knowledge(root, output)
    assert bundle.primitives
    role_reversal = next(item for item in bundle.primitives if item.primitive_id == "QP_SUPPORT_ROLE_REVERSAL")
    assert role_reversal.provenance == "PROXY_DERIVED"
    assert role_reversal.quantization_status == "PROXY_ALLOWED"
    assert any(item.primitive == role_reversal.primitive_id for item in bundle.hypotheses)
    assert (output / "quant_knowledge_bundle.jsonl").exists()
    manifest = json.loads((output / "source_manifest.json").read_text(encoding="utf-8"))
    assert manifest["export_hash"] == bundle.export_hash
    assert all(item.get("research_only") for item in bundle.quantization_proposals)


def test_export_hash_is_stable_across_output_and_source_paths(tmp_path: Path) -> None:
    hashes = []
    for suffix in ("one", "two"):
        root = tmp_path / suffix / "qinxiongmao"
        (root / "agent_corpus").mkdir(parents=True)
        (root / "strategy_research").mkdir(parents=True)
        unit = {"unit_id": "KU-1", "video_id": "v", "content": "突破回踩", "topic": ["Breakout"]}
        (root / "agent_corpus" / "knowledge_units.jsonl").write_text(json.dumps(unit) + "\n", encoding="utf-8")
        (root / "strategy_research" / "rule_candidates.jsonl").write_text("", encoding="utf-8")
        hashes.append(export_quant_knowledge(root, root / "export").export_hash)
    assert hashes[0] == hashes[1]


def test_candidate_composer_limits_search_width() -> None:
    first = _primitive(primitive_id="QP_FILTER", role="FILTER")
    second = _primitive(primitive_id="QP_FILTER_2", role="FILTER")
    candidate = compose_research_candidate("HTF_BREAK_RETEST", filters=[first])
    assert candidate["promotion_authorized"] is False
    with pytest.raises(ValueError, match="CANDIDATE_COMPOSITION_LIMIT"):
        compose_research_candidate("HTF_BREAK_RETEST", filters=[first, second])


def test_forward_distribution_reports_costed_return_mfe_and_mae() -> None:
    bars = tuple(
        EventBar(
            time=datetime(2025, 1, 1, 0, index, tzinfo=UTC),
            open=100 + index,
            high=102 + index,
            low=99 + index,
            close=101 + index,
            volume=1,
        )
        for index in range(4)
    )
    event = EdgeEvent(
        event_id="E1",
        event_type="HTF_BREAK_RETEST",
        symbol="BTC/USDT",
        side="long",
        event_time=bars[0].time,
        entry_time=bars[0].time,
        entry=100,
        stop=99,
        target=102,
        atr=1,
        volume_ratio=1,
        breakout_distance_atr=1,
        atr_percentile=0.5,
        trend_age=1,
        chop_score=0.1,
        retest_depth_atr=0.1,
        regime_4h="long",
        trend_strength_1h=1,
        outcome="WIN",
        outcome_time=bars[1].time,
        outcome_r=1,
        cost_r=0,
        mfe_r=1,
        mae_r=-1,
    )
    from services.research.quant_knowledge.runner import ResearchRecord

    result = _forward_distribution(ResearchRecord(event, bars, bars, 0, {}), 2)
    assert result is not None
    assert result[0] == pytest.approx(0.0288)
    assert result[1] == pytest.approx(0.04)
    assert result[2] == pytest.approx(0.0)


def test_bootstrap_delta_is_deterministic() -> None:
    first = _bootstrap_delta([0.01, 0.02], [0.03, 0.04], rounds=100)
    second = _bootstrap_delta([0.01, 0.02], [0.03, 0.04], rounds=100)
    assert first == second


def test_v2_hypothesis_contract_is_explicit_and_hashed() -> None:
    hypothesis = ResearchHypothesis(
        hypothesis_id="HYP-QK-V2-1",
        claim="claim",
        base_event="HTF_BREAK_RETEST",
        primitive="QP_TEST",
        metric="net_expectancy",
        research_design_version=2,
        experiment_type="INCREMENTAL_FILTER",
        parent_universe={"selector": "base_event"},
        baseline_selector={"event_type": "HTF_BREAK_RETEST"},
        candidate_selector={"primitive_id": "QP_TEST", "predicate": "x >= 1"},
        parameter_space={"x": {"values": [1, 2]}},
        feature_formula_hash="formula-hash",
        symbols=["BTC/USDT"],
        timeframes=["15m", "1h", "4h"],
    )
    assert hypothesis.hypothesis_hash
    spec = (
        HypothesisRegistry()
        .register(hypothesis)
        .to_experiment_spec(dataset_id="d", dataset_hash="dh", strategy_hash="sh")
    )
    assert spec.engine_options["research_design_version"] == 2
    assert spec.engine_options["feature_formula_hash"] == "formula-hash"


def test_tautological_role_reversal_design_is_rejected() -> None:
    assert (
        _validate_experiment_design(
            primitive_id="QP_SUPPORT_ROLE_REVERSAL",
            base_event="HTF_BREAK_RETEST",
            baseline_ids=["a", "b"],
            candidate_ids=["a", "b"],
            event_admission_features={"event_type": "HTF_BREAK_RETEST"},
        )
        == "TAUTOLOGY_FAIL"
    )


def test_event_admission_overlap_is_invalid_for_regime_filters() -> None:
    assert (
        _validate_experiment_design(
            primitive_id="QP_TREND_EMA_ALIGNMENT",
            base_event="HTF_BREAK_RETEST",
            baseline_ids=["a"],
            candidate_ids=["a"],
            event_admission_features={"uses_regime_4h": True, "uses_trend_strength_1h": True},
        )
        == "TAUTOLOGY_FAIL"
    )


def test_paired_bootstrap_reapplies_predicate_to_resampled_baseline() -> None:
    rows = [(0.01, True), (0.02, False), (-0.01, True), (-0.02, False)]
    result = _paired_bootstrap_delta(rows, seed=7, rounds=100)
    assert result["sample_count"] == 4
    assert result["candidate_sample_count"] <= 4
    assert result["mean_delta"] != 0.0


def test_progress_accounts_deferred_primitives_without_unknown() -> None:
    primitive = _primitive(primitive_id="QP_DEFERRED")
    hypothesis = ResearchHypothesis(
        hypothesis_id="HYP-DEFERRED",
        claim="claim",
        base_event="HTF_BREAK_RETEST",
        primitive="QP_DEFERRED",
        metric="net_expectancy",
    )
    progress: dict[str, Any] = {"hypothesis_status": {}, "primitive_status": {}}
    _rebuild_progress(progress, [hypothesis], {primitive.primitive_id: primitive})
    assert progress["primitives"]["accounted"] == 0
    assert progress["primitives"]["deferred"] == 1
    assert progress["hypotheses"]["unknown"] == 0
