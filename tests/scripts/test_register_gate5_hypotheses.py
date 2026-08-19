import pytest

from scripts.register_gate5_hypotheses import hypotheses_from_evidence


def test_gate5_hypotheses_are_pre_registered_from_direction_and_expansion_evidence() -> None:
    trials = hypotheses_from_evidence(
        {
            "failure_root_cause_ranking": [{"root_cause": "DIRECTION_FAILURE"}],
            "slices": {"market_regime": {"EXPANSION": {"episodes": 9, "net_before_funding_usdt": "1.2"}}},
        }
    )

    assert [trial.hypothesis_id for trial in trials] == ["G5-H1-EXPANSION-REGIME-V1", "G5-H2-BREAKOUT-RETEST-V1"]
    assert all(trial.created_before_result and not trial.final_holdout_accessed for trial in trials)


def test_gate5_hypotheses_refuse_to_invent_a_regime_signal_without_evidence() -> None:
    with pytest.raises(ValueError, match="DIRECTION_FAILURE"):
        hypotheses_from_evidence({"failure_root_cause_ranking": [], "slices": {"market_regime": {}}})
