from __future__ import annotations

import json
import subprocess

from services.research.integrations import (
    FreqtradeValidationAdapter,
    ResearchContextBundle,
    ResearchCouncil,
    ResearchExperimentResult,
    ResearchExperimentSpec,
    ResearchOrchestrator,
    VectorbtScreenAdapter,
)


def _spec() -> ResearchExperimentSpec:
    return ResearchExperimentSpec(
        strategy_id="candidate",
        strategy_hash="strategy-hash",
        dataset_id="dataset",
        dataset_hash="dataset-hash",
        parameter_space={"entry": [1, 2], "exit": [1.5, 2.0]},
        cost_model={"fee_bps": 5},
    )


def test_contract_hashes_are_deterministic() -> None:
    first = _spec()
    second = _spec()
    assert first.input_spec_hash == second.input_spec_hash
    assert first.cost_model_hash == second.cost_model_hash


def test_vectorbt_screen_reports_plateau_and_multiple_candidates() -> None:
    payload = {
        "trade_count": 3,
        "win_rate": 2 / 3,
        "payoff_ratio": 2.0,
        "profit_factor": 4.0,
        "expectancy_net_r": 0.01,
        "max_drawdown": 0.02,
        "parameter_plateau": {
            "top_candidates": [{"parameters": {"entry": 1}}, {"parameters": {"entry": 2}}],
            "neighbor_stability": 1.0,
        },
    }

    def runner(command, **_kwargs):
        return subprocess.CompletedProcess(
            command, 0, stdout="" if command[1] == "-c" else json.dumps(payload), stderr=""
        )

    result = VectorbtScreenAdapter(python_executable="vectorbt-python", runner=runner).screen(
        _spec(),
        [{"return": 0.02}, {"return": -0.01}, {"return": 0.03}],
        run_id="run-1",
    )
    assert result.status == "completed"
    assert len(result.parameter_plateau["top_candidates"]) > 1
    assert "neighbor_stability" in result.parameter_plateau


def test_orchestrator_enforces_bias_gate_and_never_authorizes() -> None:
    result = ResearchOrchestrator().run_pipeline(_spec(), [{"return": 0.01}], run_id="run-2")
    assert result["status"] == "failed"
    assert result["stage"] == "vectorbt_screen"
    assert result["failure_reason"] in {
        "VECTORBT_UNAVAILABLE",
        "ValueError: VECTORBT_CANONICAL_CLOSE_AND_SIGNAL_COLUMNS_REQUIRED",
    }


def test_orchestrator_surfaces_freqtrade_subprocess_failure() -> None:
    class PassingVectorbt:
        def screen(self, spec, rows, *, run_id):
            return ResearchExperimentResult(
                run_id=run_id,
                engine="vectorbt",
                input_spec_hash=spec.input_spec_hash,
                dataset_hash=spec.dataset_hash,
                cost_model_hash=spec.cost_model_hash,
                strategy_hash=spec.strategy_hash,
                status="completed",
                parameter_plateau={"top_candidates": [{"parameters": {"entry": 1}}]},
            )

    class FailingFreqtrade:
        def validate(self, spec, rows, *, run_id, candidate=None):
            return ResearchExperimentResult(
                run_id=run_id,
                engine="freqtrade",
                input_spec_hash=spec.input_spec_hash,
                dataset_hash=spec.dataset_hash,
                cost_model_hash=spec.cost_model_hash,
                strategy_hash=spec.strategy_hash,
                status="failed",
                failure_reason="FREQTRADE_UNAVAILABLE",
            )

    result = ResearchOrchestrator(vectorbt=PassingVectorbt(), freqtrade=FailingFreqtrade()).run_pipeline(
        _spec(),
        [{"close": 100, "entry_signal": True, "exit_signal": False}],
        run_id="run-freqtrade-failure",
    )
    assert result["stage"] == "freqtrade_validation"
    assert result["failure_reason"] == "FREQTRADE_UNAVAILABLE"


def test_freqtrade_result_marks_bias_checks_explicitly() -> None:
    result = FreqtradeValidationAdapter(executable="freqtrade").validate(_spec(), [{"return": 0.01}], run_id="run-3")
    assert result.status == "failed"
    assert result.failure_reason == "FREQTRADE_CONFIGURATION_REQUIRED"
    assert result.provenance["trade_command_allowed"] is False


def test_context_preserves_missing_data_semantics() -> None:
    bundle = ResearchContextBundle.from_records(
        [{"family": "news", "source": "rss", "status": "missing", "missing_reason": "provider_timeout"}],
        symbol="BTC/USDT",
    )
    assert bundle.has_unavailable_data is True
    assert bundle.items[0].missing_reason == "provider_timeout"


def test_council_has_evidence_and_no_order_side_effect() -> None:
    verdict = ResearchCouncil().review("candidate", {"evidence_refs": ["run:1"]})
    assert verdict.verdict == "accept_for_next_gate"
    assert verdict.order_side_effects is False
    assert set(verdict.roles) == set(ResearchCouncil.roles)
