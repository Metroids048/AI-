from __future__ import annotations

import importlib
from pathlib import Path


def _by_variant(results):
    return {result.variant: result for result in results}


def test_shadow_ablation_changes_one_filter_at_a_time() -> None:
    module_path = Path("services/review/strategy_ablation.py")
    assert module_path.is_file(), "Review-layer shadow ablation evaluator is not implemented"
    evaluate = importlib.import_module("services.review.strategy_ablation").evaluate_shadow_variants
    trace = {
        "pipeline_status": "ensemble_discarded",
        "signals": [
            {"side": "long", "confidence": 0.8, "source": "trend"},
            {"side": "short", "confidence": 0.2, "source": "reversal"},
        ],
        "ensemble": {
            "ensemble_status": "discarded_low_confidence",
            "fused_direction": None,
        },
        "veto_result": None,
        "volatility": {
            "multi_timeframe": {
                "passed": True,
                "status": "confirmed",
                "main_direction": "long",
            }
        },
    }

    results = _by_variant(evaluate(trace))

    assert results["A_CURRENT_PRODUCTION"].candidate is False
    assert results["B_NO_LLM_HARD_VETO"].candidate is False
    assert results["C_WEIGHTED_ENSEMBLE"].candidate is True
    assert results["C_WEIGHTED_ENSEMBLE"].side == "long"
    assert results["C_WEIGHTED_ENSEMBLE"].long_weight == 0.8
    assert results["C_WEIGHTED_ENSEMBLE"].short_weight == 0.2
    assert results["D_HIERARCHICAL_MTF"].candidate is False
    assert results["E_COMBINED_BCD"].candidate is True


def test_shadow_ablation_makes_llm_advisory_without_changing_other_filters() -> None:
    evaluate = importlib.import_module("services.review.strategy_ablation").evaluate_shadow_variants
    trace = {
        "pipeline_status": "vetoed",
        "signals": [{"side": "long", "confidence": 0.7, "source": "trend"}],
        "ensemble": {
            "ensemble_status": "passed_to_meta_label",
            "fused_direction": "long",
            "fused_confidence": 0.7,
        },
        "meta_label": {"bet_decision": "bet_taken"},
        "veto_result": {"veto": True, "veto_reason": "ambiguous context"},
        "volatility": {"multi_timeframe": {"passed": True, "status": "confirmed"}},
    }

    results = _by_variant(evaluate(trace))

    assert results["A_CURRENT_PRODUCTION"].candidate is False
    assert results["B_NO_LLM_HARD_VETO"].candidate is True
    assert results["B_NO_LLM_HARD_VETO"].side == "long"
    assert results["C_WEIGHTED_ENSEMBLE"].candidate is False
    assert results["D_HIERARCHICAL_MTF"].candidate is False
    assert results["E_COMBINED_BCD"].candidate is True
    assert results["E_COMBINED_BCD"].llm_advisory is True


def test_shadow_ablation_returns_unknown_when_hierarchical_mtf_evidence_was_not_persisted() -> None:
    evaluate = importlib.import_module("services.review.strategy_ablation").evaluate_shadow_variants
    trace = {
        "pipeline_status": "multi_timeframe_disagreement",
        "signals": [{"side": "short", "confidence": 0.7, "source": "entry"}],
        "ensemble": None,
        "veto_result": None,
        "volatility": {
            "multi_timeframe": {
                "passed": False,
                "status": "state_confirmation_disagreed",
                "main_direction": "short",
                "state_direction": "long",
            }
        },
    }

    results = _by_variant(evaluate(trace))

    assert results["D_HIERARCHICAL_MTF"].candidate is None
    assert results["E_COMBINED_BCD"].candidate is None
    assert "4h" in results["D_HIERARCHICAL_MTF"].evidence_gaps
