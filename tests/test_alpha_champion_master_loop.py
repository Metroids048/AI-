from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from scripts import run_proposal_research_replay as proposal_research
from scripts.run_alpha_champion_master_loop import (
    PROPOSAL_CANDIDATES,
    CandidateInventoryRecord,
    TerminalStatus,
    TournamentDisposition,
    VariantSpec,
    _apply_funding_cost,
    _bounded_search_execution,
    _build_research_validation_leaderboard,
    _candidate_passes,
    _expensive_validation_pending,
    _generation_one_specs,
    _generation_two_specs,
    _merge_research_validation_result,
    _no_alpha_allowed,
    _recovery_metrics_for_result,
    _research_passes,
    _run_base_validation,
    _run_expensive_validations,
    _search_surface_exhausted,
    audit_market_data,
    bounded_search_plan,
    build_dual_gate_report,
    discover_candidate_inventory,
    run_generation_zero,
    run_master_loop,
    tournament_candidate_ids,
    unclassified_unreachable_candidates,
)


def _split() -> proposal_research.ProposalWalkForwardWindow:
    return proposal_research.ProposalWalkForwardWindow(
        window_id="w0",
        train_start=datetime(2025, 1, 1, tzinfo=UTC),
        train_end=datetime(2025, 6, 1, tzinfo=UTC),
        purge_start=datetime(2025, 6, 1, tzinfo=UTC),
        purge_end=datetime(2025, 6, 2, tzinfo=UTC),
        oos_start=datetime(2025, 6, 2, tzinfo=UTC),
        oos_end=datetime(2025, 7, 1, tzinfo=UTC),
        embargo_start=datetime(2025, 7, 1, tzinfo=UTC),
        embargo_end=datetime(2025, 7, 2, tzinfo=UTC),
    )


def _database(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE ohlcv_bars (time TEXT, symbol TEXT, timeframe TEXT, "
        "open REAL, high REAL, low REAL, close REAL, volume REAL)"
    )
    for symbol in ("BTC/USDT", "ETH/USDT"):
        for timeframe, stamp in (
            ("15m", "2026-01-01 00:00:00"),
            ("1h", "2026-01-01 00:00:00"),
            ("4h", "2026-01-01 00:00:00"),
        ):
            connection.execute(
                "INSERT INTO ohlcv_bars VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (stamp, symbol, timeframe, 100, 101, 99, 100, 10),
            )
    connection.commit()
    connection.close()


def test_all_proposal_candidates_are_registered_and_reachable() -> None:
    records = {record.candidate_id: record for record in discover_candidate_inventory()}

    assert records.keys() >= PROPOSAL_CANDIDATES
    assert all(records[candidate_id].registered for candidate_id in PROPOSAL_CANDIDATES)
    assert all(records[candidate_id].canonical_replay_reachable for candidate_id in PROPOSAL_CANDIDATES)
    assert all(records[candidate_id].eligible_for_tournament for candidate_id in PROPOSAL_CANDIDATES)
    assert all(not records[candidate_id].execution_eligible for candidate_id in PROPOSAL_CANDIDATES)


def test_tournament_includes_registry_controls_but_excludes_canary() -> None:
    candidate_ids = tournament_candidate_ids(discover_candidate_inventory())

    assert "trend_momentum_v1" in candidate_ids
    assert "trend_breakout_v1" in candidate_ids
    assert "operator_heuristic_v2_relaxed" in candidate_ids
    assert "testnet_sampling_v2" not in candidate_ids


def test_registry_only_candidates_have_explicit_exclusion_dispositions() -> None:
    records = {record.candidate_id: record for record in discover_candidate_inventory()}

    superseded = records["trend_pullback_v1"]
    assert superseded.registered is True
    assert superseded.canonical_replay_reachable is False
    assert superseded.tournament_disposition == TournamentDisposition.SUPERSEDED.value
    assert superseded.eligible_for_tournament is False
    assert superseded.superseded_by == "trend_pullback_v2"
    assert superseded.exclusion_reason

    stub = records["aggressive_multi_regime_v1"]
    assert stub.registered is True
    assert stub.canonical_replay_reachable is False
    assert stub.tournament_disposition == TournamentDisposition.UNIMPLEMENTED_DESIGN_STUB.value
    assert stub.eligible_for_tournament is False
    assert stub.exclusion_reason


def test_explicitly_excluded_registry_candidates_do_not_enter_tournament() -> None:
    candidate_ids = tournament_candidate_ids(discover_candidate_inventory())

    assert "trend_pullback_v1" not in candidate_ids
    assert "aggressive_multi_regime_v1" not in candidate_ids
    assert "trend_pullback_v2" in candidate_ids


def test_unknown_unreachable_registered_candidate_still_blocks_baseline(monkeypatch, tmp_path: Path) -> None:
    module = __import__("scripts.run_alpha_champion_master_loop", fromlist=["run_master_loop"])
    unknown = CandidateInventoryRecord(
        candidate_id="unknown_registry_candidate",
        version="0.0.0",
        family="other",
        registered=True,
        evaluator_path=None,
        canonical_replay_reachable=False,
        research_only=True,
        execution_eligible=False,
        symbols=("BTC/USDT", "ETH/USDT"),
        timeframe="15m",
        entry_contract="proposal",
    )
    monkeypatch.setattr(module, "discover_candidate_inventory", lambda: (unknown,))
    database = tmp_path / "market.db"
    _database(database)

    result = module.run_master_loop(root=Path.cwd(), database=database, output=tmp_path / "output")

    assert result["status"] == TerminalStatus.BLOCKED_BASELINE.value
    assert result["reason"] == "candidate_path_unreachable"


def test_explicit_exclusions_do_not_block_baseline() -> None:
    inventory = discover_candidate_inventory()
    assert unclassified_unreachable_candidates(inventory) == ()


def test_dual_gate_report_never_claims_final_acceptance_from_one_gate() -> None:
    report = build_dual_gate_report(
        execution_chain={"status": "PASS", "evidence": ["natural-proof"]},
        profitability_recovery={"status": "BLOCKED", "evidence": ["no-champion"]},
    )

    assert report["overall_status"] == "PENDING"


def test_technical_funding_replay_is_point_in_time_and_explicit_when_missing() -> None:
    opened = datetime(2026, 1, 1, tzinfo=UTC)
    closed = datetime(2026, 1, 1, 12, tzinfo=UTC)
    trade = {
        "opened_at": opened.isoformat(),
        "closed_at": closed.isoformat(),
        "side": "long",
        "net_return": 0.01,
        "quantity_fraction": 1.0,
    }

    enriched, observed = _apply_funding_cost(
        trade,
        (
            (datetime(2025, 12, 31, 16, tzinfo=UTC), Decimal("0.002")),
            (datetime(2026, 1, 1, 8, tzinfo=UTC), Decimal("0.001")),
        ),
    )
    assert observed is True
    assert enriched["funding_evidence"] == "POINT_IN_TIME_OBSERVED"
    assert enriched["funding_cost"] == pytest.approx(0.001)
    assert enriched["net_return"] == pytest.approx(0.009)

    missing, observed = _apply_funding_cost(trade, ())
    assert observed is False
    assert missing["funding_evidence"] == "MISSING"
    assert missing["funding_cost"] is None


def test_screening_gate_does_not_require_final_evidence_but_research_gate_requires_funding() -> None:
    screening = {
        "trades": 80,
        "net_expectancy": 0.01,
        "net_return": 0.8,
        "profit_factor": 1.3,
        "positive_windows": 5,
        "funding_observed": False,
    }
    assert _candidate_passes(screening) is True
    assert _research_passes(screening) is False
    screening["funding_observed"] = True
    assert _research_passes(screening) is True


def test_technical_cost_evidence_does_not_promote_configured_slippage_to_observed() -> None:
    result = {
        "portfolio": {
            "total_trades": 80,
            "net_return": 0.8,
            "net_expectancy": 0.01,
            "profit_factor": 1.3,
            "funding_rate_available": True,
        },
        "symbols": {
            "BTC/USDT": {"total_trades": 40, "profit_factor": 1.2},
            "ETH/USDT": {"total_trades": 40, "profit_factor": 1.2},
        },
        "trades": [{"slippage_bps": 5, "net_return": 0.01}],
        "cost_evidence": {"slippage_observed": False},
    }
    metrics = _recovery_metrics_for_result(result)
    assert metrics.funding_observed is True
    assert metrics.slippage_observed is False


def test_generation_zero_checkpoint_skips_completed_candidate_and_reuses_cache(tmp_path: Path, monkeypatch) -> None:
    database = tmp_path / "market.db"
    _database(database)
    output = tmp_path / "master"
    window = proposal_research.ProposalWalkForwardWindow(
        window_id="w0",
        train_start=datetime(2025, 1, 1, tzinfo=UTC),
        train_end=datetime(2025, 6, 1, tzinfo=UTC),
        purge_start=datetime(2025, 6, 1, tzinfo=UTC),
        purge_end=datetime(2025, 6, 2, tzinfo=UTC),
        oos_start=datetime(2025, 6, 2, tzinfo=UTC),
        oos_end=datetime(2025, 7, 1, tzinfo=UTC),
        embargo_start=datetime(2025, 7, 1, tzinfo=UTC),
        embargo_end=datetime(2025, 7, 2, tzinfo=UTC),
    )
    record = CandidateInventoryRecord(
        candidate_id="operator_heuristic_v1",
        version="test",
        family="operator",
        registered=True,
        evaluator_path="test",
        canonical_replay_reachable=True,
        research_only=True,
        execution_eligible=False,
        symbols=("BTC/USDT", "ETH/USDT"),
        timeframe="15m",
        entry_contract="test",
    )
    calls = {"replay": 0, "market": 0, "funding": 0}

    def fake_replay(**kwargs):
        calls["replay"] += 1
        return {
            "candidate_id": "operator_heuristic_v1",
            "portfolio": {
                "total_trades": 0,
                "net_return": 0.0,
                "net_expectancy": 0.0,
                "profit_factor": 0.0,
                "funding_rate_available": False,
            },
            "symbols": {},
            "trades": [],
            "walk_forward_oos": {},
        }

    module = __import__("scripts.run_alpha_champion_master_loop", fromlist=["run_generation_zero"])
    monkeypatch.setattr(module, "discover_candidate_inventory", lambda: (record,))
    monkeypatch.setattr(module, "_technical_candidate_result", fake_replay)
    original_market = module._load_technical_market_data
    original_funding = module._load_funding_points

    def cached_market(*args, **kwargs):
        calls["market"] += 1
        return original_market(*args, **kwargs)

    def cached_funding(*args, **kwargs):
        calls["funding"] += 1
        return original_funding(*args, **kwargs)

    monkeypatch.setattr(module, "_load_technical_market_data", cached_market)
    monkeypatch.setattr(module, "_load_funding_points", cached_funding)

    run_generation_zero(database, output, ("operator_heuristic_v1",), windows=(window,))
    assert calls == {"replay": 1, "market": 1, "funding": 1}

    partial = json.loads((output / "GENERATION_0_PARTIAL.json").read_text(encoding="utf-8"))
    assert partial["completed_candidate_ids"] == ["operator_heuristic_v1"]

    run_generation_zero(database, output, ("operator_heuristic_v1",), windows=(window,))
    assert calls == {"replay": 1, "market": 1, "funding": 1}


def test_generation_zero_resume_builds_only_pending_proposals(tmp_path: Path, monkeypatch) -> None:
    output = tmp_path / "master"
    database = tmp_path / "market.db"
    calls: list[tuple[str, ...]] = []
    window = proposal_research.ProposalWalkForwardWindow(
        window_id="w0",
        train_start=datetime(2025, 1, 1, tzinfo=UTC),
        train_end=datetime(2025, 6, 1, tzinfo=UTC),
        purge_start=datetime(2025, 6, 1, tzinfo=UTC),
        purge_end=datetime(2025, 6, 2, tzinfo=UTC),
        oos_start=datetime(2025, 6, 2, tzinfo=UTC),
        oos_end=datetime(2025, 7, 1, tzinfo=UTC),
        embargo_start=datetime(2025, 7, 1, tzinfo=UTC),
        embargo_end=datetime(2025, 7, 2, tzinfo=UTC),
    )
    completed = {
        "candidate_id": "proposal_a",
        "portfolio": {"total_trades": 1, "net_return": 1.0, "net_expectancy": 1.0, "profit_factor": 2.0},
        "symbols": {},
        "trades": [],
        "walk_forward_oos": {},
    }
    (output / "GENERATION_0_PARTIAL.json").parent.mkdir(parents=True)
    (output / "GENERATION_0_PARTIAL.json").write_text(
        json.dumps({"results": {"proposal_a": completed}}), encoding="utf-8"
    )
    monkeypatch.setattr(
        module := __import__("scripts.run_alpha_champion_master_loop", fromlist=["run_generation_zero"]),
        "PROPOSAL_CANDIDATES",
        frozenset({"proposal_a", "proposal_b"}),
    )
    monkeypatch.setattr(module, "TOURNAMENT_CONTROL_CANDIDATES", frozenset())
    records = tuple(
        CandidateInventoryRecord(
            candidate_id=item,
            version="test",
            family="test",
            registered=True,
            evaluator_path="proposal",
            canonical_replay_reachable=True,
            research_only=True,
            execution_eligible=False,
            symbols=("BTC/USDT", "ETH/USDT"),
            timeframe="15m",
            entry_contract="proposal",
        )
        for item in ("proposal_a", "proposal_b")
    )
    monkeypatch.setattr(module, "discover_candidate_inventory", lambda: records)

    def build(**kwargs):
        calls.append(tuple(kwargs["candidate_ids"]))
        return ()

    monkeypatch.setattr(proposal_research, "_build_window_runs", build)
    monkeypatch.setattr(
        proposal_research,
        "_result_for_candidate",
        lambda **kwargs: completed | {"candidate_id": kwargs["candidate_id"]},
    )
    result = module.run_generation_zero(database, output, ("proposal_a", "proposal_b"), windows=(window,), resume=True)
    assert calls == [("proposal_b",)]
    assert set(result["results"]) == {"proposal_a", "proposal_b"}


def test_validation_dispatches_technical_controls_to_technical_replay(tmp_path: Path, monkeypatch) -> None:
    module = __import__("scripts.run_alpha_champion_master_loop", fromlist=["_run_base_validation"])
    window = proposal_research.ProposalWalkForwardWindow(
        window_id="validation",
        train_start=datetime(2025, 1, 1, tzinfo=UTC),
        train_end=datetime(2025, 6, 1, tzinfo=UTC),
        purge_start=datetime(2025, 6, 1, tzinfo=UTC),
        purge_end=datetime(2025, 6, 2, tzinfo=UTC),
        oos_start=datetime(2025, 6, 2, tzinfo=UTC),
        oos_end=datetime(2025, 7, 1, tzinfo=UTC),
        embargo_start=datetime(2025, 7, 1, tzinfo=UTC),
        embargo_end=datetime(2025, 7, 2, tzinfo=UTC),
    )
    monkeypatch.setattr(module, "TOURNAMENT_CONTROL_CANDIDATES", frozenset({"trend_momentum_v1"}))
    monkeypatch.setattr(
        module,
        "_technical_candidate_result",
        lambda **kwargs: {
            "portfolio": {"total_trades": 80, "net_return": 1.0, "net_expectancy": 0.01, "profit_factor": 1.5},
            "walk_forward_oos": {},
        },
    )
    monkeypatch.setattr(
        proposal_research, "_build_window_runs", lambda **kwargs: (_ for _ in ()).throw(AssertionError())
    )
    result = _run_base_validation(
        database=tmp_path / "missing.db",
        output=tmp_path,
        candidate_id="trend_momentum_v1",
        windows=(window,),
        data_end=window.oos_end,
    )
    assert result["evaluation_stage"] == "validation"


def test_finalist_evidence_merges_oos_and_records_expensive_validation(tmp_path: Path) -> None:
    research = {
        "portfolio": {"total_trades": 2},
        "symbols": {},
        "trades": [
            {"symbol": "BTC/USDT", "gross_return": 0.02, "net_return": 0.01},
        ],
        "walk_forward_oos": {"research_1": {"symbols": {}}},
    }
    validation = {
        "portfolio": {"total_trades": 2},
        "symbols": {},
        "trades": [
            {"symbol": "ETH/USDT", "gross_return": 0.01, "net_return": 0.005},
        ],
        "walk_forward_oos": {"validation": {"symbols": {}}},
    }
    merged = _merge_research_validation_result(research, validation)
    evidence = _run_expensive_validations(candidate_id="candidate", result=merged, output=tmp_path)
    metrics = _recovery_metrics_for_result(merged, final_holdout={"net_expectancy": 0.001}, expensive_evidence=evidence)
    assert metrics.total_trades == 2
    assert set(merged["walk_forward_oos"]) == {"research_1", "validation"}
    assert evidence["bootstrap"]["status"] == "COMPLETED"
    assert (tmp_path / "EXPENSIVE_VALIDATION_candidate.json").is_file()
    assert _expensive_validation_pending(evidence) is True


def test_bounded_search_plan_cannot_expand_generation_budget() -> None:
    plan = bounded_search_plan(discover_candidate_inventory())

    assert plan["max_generation"] == 2
    assert plan["max_variables_per_family"] == 2
    assert "generation_3_plus" in plan["forbidden"]


def test_generation_one_specs_are_ofat_and_within_declared_cap() -> None:
    specs = _generation_one_specs("volatility_expansion_v1")

    assert specs
    assert all(spec.generation == 1 for spec in specs)
    assert all(len(spec.changed_parameters) == 1 for spec in specs)
    assert len({spec.parameters["compression_ratio"] for spec in specs}) <= 3
    assert len({spec.parameters["breakout_body_atr"] for spec in specs}) <= 3


def test_generation_two_is_hard_capped_at_two_single_changes() -> None:
    specs = _generation_two_specs(
        {
            "results": {
                "one": {
                    "variant": {
                        "variant_id": "one",
                        "parent_candidate": "volatility_expansion_v1",
                        "family": "breakout",
                        "generation": 1,
                        "hypothesis": "x",
                        "parameters": {"compression_ratio": 0.7, "breakout_body_atr": 0.8},
                        "changed_parameters": ("compression_ratio",),
                    },
                    "master_metrics": {"net_expectancy": 0.1},
                },
                "two": {
                    "variant": {
                        "variant_id": "two",
                        "parent_candidate": "trend_pullback_v2",
                        "family": "trend",
                        "generation": 1,
                        "hypothesis": "x",
                        "parameters": {"maximum_entry_distance_atr": 0.35, "minimum_trend_score": 0.55},
                        "changed_parameters": ("maximum_entry_distance_atr",),
                    },
                    "master_metrics": {"net_expectancy": 0.05},
                },
                "three": {
                    "variant": {
                        "variant_id": "three",
                        "parent_candidate": "momentum_continuation_v1",
                        "family": "momentum",
                        "generation": 1,
                        "hypothesis": "x",
                        "parameters": {"momentum_bars": 2, "minimum_move_atr": 0.75},
                        "changed_parameters": ("momentum_bars",),
                    },
                    "master_metrics": {"net_expectancy": 0.01},
                },
            }
        }
    )

    assert len(specs) == 2
    assert all(spec.generation == 2 and len(spec.changed_parameters) == 1 for spec in specs)


def test_generation_one_executes_full_declared_ofat_surface(tmp_path: Path, monkeypatch) -> None:
    module = __import__("scripts.run_alpha_champion_master_loop", fromlist=["run_generation_one"])
    parent = "volatility_expansion_v1"
    specs = tuple(
        VariantSpec(
            variant_id=f"{parent}@g1:{index}",
            parent_candidate=parent,
            family="breakout",
            generation=1,
            hypothesis="bounded",
            parameters={("compression_ratio" if index < 2 else "breakout_body_atr"): 0.7 + index / 10},
            changed_parameters=(("compression_ratio",) if index < 2 else ("breakout_body_atr",)),
        )
        for index in range(4)
    )
    monkeypatch.setattr(module, "_generation_one_specs", lambda candidate_id: specs)
    monkeypatch.setattr(module, "_research_windows", lambda split: ())
    calls: list[str] = []

    def fake_run_variant(**kwargs):
        spec = kwargs["spec"]
        calls.append(spec.variant_id)
        return {"variant": spec.as_record(), "master_metrics": {"net_expectancy": 0.01}}

    monkeypatch.setattr(module, "_run_variant", fake_run_variant)
    split = type("Split", (), {"research_end": datetime(2025, 1, 1, tzinfo=UTC)})()
    result = module.run_generation_one(
        database=tmp_path / "market.db",
        output=tmp_path / "output",
        split=split,
        generation_zero={
            "results": {
                parent: {
                    "master_metrics": {
                        "trades": 100,
                        "net_expectancy": 0.01,
                    }
                }
            }
        },
    )

    assert len(result["variants"]) == 4
    assert set(calls) == {spec.variant_id for spec in specs}


def test_generation_one_does_not_exceed_declared_surface(tmp_path: Path, monkeypatch) -> None:
    module = __import__("scripts.run_alpha_champion_master_loop", fromlist=["run_generation_one"])
    parent = "volatility_expansion_v1"
    specs = tuple(
        VariantSpec(
            variant_id=f"{parent}@g1:{index}",
            parent_candidate=parent,
            family="breakout",
            generation=1,
            hypothesis="bounded",
            parameters={("compression_ratio" if index < 2 else "breakout_body_atr"): 0.7 + index / 10},
            changed_parameters=(("compression_ratio",) if index < 2 else ("breakout_body_atr",)),
        )
        for index in range(4)
    )
    monkeypatch.setattr(module, "_generation_one_specs", lambda candidate_id: specs)
    monkeypatch.setattr(module, "_research_windows", lambda split: ())
    monkeypatch.setattr(
        module,
        "_run_variant",
        lambda **kwargs: {"variant": kwargs["spec"].as_record(), "master_metrics": {"net_expectancy": 0.01}},
    )
    split = type("Split", (), {"research_end": datetime(2025, 1, 1, tzinfo=UTC)})()
    result = module.run_generation_one(
        database=tmp_path / "market.db",
        output=tmp_path / "output",
        split=split,
        generation_zero={
            "results": {
                parent: {
                    "master_metrics": {
                        "trades": 100,
                        "net_expectancy": 0.01,
                    }
                }
            }
        },
    )

    assert len(result["variants"]) == len(specs)
    assert len({item["variant_id"] for item in result["variants"]}) == len(specs)


def test_resume_runs_only_pending_generation_one_variants(tmp_path: Path, monkeypatch) -> None:
    module = __import__("scripts.run_alpha_champion_master_loop", fromlist=["run_generation_one"])
    parent = "volatility_expansion_v1"
    specs = tuple(
        VariantSpec(
            variant_id=f"{parent}@g1:{index}",
            parent_candidate=parent,
            family="breakout",
            generation=1,
            hypothesis="bounded",
            parameters={("compression_ratio" if index < 2 else "breakout_body_atr"): 0.7 + index / 10},
            changed_parameters=(("compression_ratio",) if index < 2 else ("breakout_body_atr",)),
        )
        for index in range(4)
    )
    monkeypatch.setattr(module, "_generation_one_specs", lambda candidate_id: specs)
    monkeypatch.setattr(module, "_research_windows", lambda split: ())
    calls: list[str] = []

    def fake_run_variant(**kwargs):
        spec = kwargs["spec"]
        calls.append(spec.variant_id)
        return {"variant": spec.as_record(), "master_metrics": {"net_expectancy": 0.01}}

    monkeypatch.setattr(module, "_run_variant", fake_run_variant)
    output = tmp_path / "output"
    output.mkdir()
    partial_results = {
        spec.variant_id: {"variant": spec.as_record(), "master_metrics": {"net_expectancy": 0.01}} for spec in specs[:2]
    }
    (output / "GENERATION_1_PARTIAL.json").write_text(
        json.dumps(
            {
                "generation": 1,
                "parents": [parent],
                "variants": [spec.as_record() for spec in specs],
                "results": partial_results,
            }
        ),
        encoding="utf-8",
    )
    split = type("Split", (), {"research_end": datetime(2025, 1, 1, tzinfo=UTC)})()
    result = module.run_generation_one(
        database=tmp_path / "market.db",
        output=output,
        split=split,
        generation_zero={
            "results": {
                parent: {
                    "master_metrics": {
                        "trades": 100,
                        "net_expectancy": 0.01,
                    }
                }
            }
        },
        resume=True,
    )

    assert calls == [spec.variant_id for spec in specs[2:]]
    assert set(result["results"]) == {spec.variant_id for spec in specs}


def test_generation_two_respects_two_hypothesis_cap() -> None:
    specs = _generation_two_specs(
        {
            "results": {
                str(i): {
                    "variant": {
                        "parent_candidate": f"candidate_{i}",
                        "parameters": {},
                        "generation": 1,
                        "family": "family",
                        "changed_parameters": (),
                    },
                    "master_metrics": {"net_expectancy": 1.0 - i / 10},
                }
                for i in range(5)
            }
        }
    )
    assert len(specs) <= 2


def test_search_surface_exhaustion_is_fail_closed() -> None:
    incomplete = _bounded_search_execution(
        planned_generation_1=["g1-a", "g1-b"],
        executed_generation_1=["g1-a"],
        validated_generation_1=["g1-a"],
        planned_generation_2=["g2-a"],
        executed_generation_2=["g2-a"],
        validated_generation_2=["g2-a"],
        entries=[],
    )
    assert incomplete["search_surface_exhausted"] is False
    assert incomplete["missing_generation_1"] == ["g1-b"]
    assert _search_surface_exhausted(incomplete) is False
    assert _no_alpha_allowed(incomplete, finalist_count=0, final_holdout_accessed=False) is False

    complete = _bounded_search_execution(
        planned_generation_1=["g1-a"],
        executed_generation_1=["g1-a"],
        validated_generation_1=["g1-a"],
        planned_generation_2=["g2-a"],
        executed_generation_2=["g2-a"],
        validated_generation_2=["g2-a"],
        entries=[],
    )
    assert _search_surface_exhausted(complete) is True
    assert _no_alpha_allowed(complete, finalist_count=0, final_holdout_accessed=False) is True
    assert _no_alpha_allowed(complete, finalist_count=1, final_holdout_accessed=False) is False
    assert _no_alpha_allowed(complete, finalist_count=0, final_holdout_accessed=True) is False


def test_research_validation_leaderboard_exposes_both_stages() -> None:
    research = {
        "portfolio": {
            "total_trades": 80,
            "profit_factor": 1.15,
            "net_expectancy": 0.01,
            "net_return": 0.8,
            "max_drawdown": 0.1,
        },
        "symbols": {"BTC/USDT": {"total_trades": 40}, "ETH/USDT": {"total_trades": 40}},
        "walk_forward_oos": {},
        "master_metrics": {
            "trades": 80,
            "profit_factor": 1.15,
            "net_expectancy": 0.01,
            "net_return": 0.8,
            "max_drawdown": 0.1,
            "positive_windows": 5,
        },
    }
    validation = {
        "portfolio": {
            "total_trades": 80,
            "profit_factor": 0.9,
            "net_expectancy": -0.01,
            "net_return": -0.8,
            "max_drawdown": 0.2,
        },
        "symbols": {"BTC/USDT": {"total_trades": 40}, "ETH/USDT": {"total_trades": 40}},
        "walk_forward_oos": {},
        "master_metrics": {
            "trades": 80,
            "profit_factor": 0.9,
            "net_expectancy": -0.01,
            "net_return": -0.8,
            "max_drawdown": 0.2,
            "positive_windows": 2,
        },
    }
    entries = _build_research_validation_leaderboard(
        [
            {
                "candidate_id": "volatility_expansion_v1",
                "generation": 0,
                "family": "breakout",
                "research": research,
                "validation": validation,
            }
        ],
    )
    assert entries[0]["research"]["profit_factor"] == pytest.approx(1.15)
    assert entries[0]["validation"]["profit_factor"] == pytest.approx(0.9)
    assert entries[0]["combined"]["finalist_eligible"] is False


def test_data_audit_rejects_missing_spacing_and_alignment(tmp_path: Path) -> None:
    database = tmp_path / "market.db"
    _database(database)

    result = audit_market_data(database)

    assert result["passed"] is False
    assert result["symbols"]["BTC/USDT"]["15m"]["rows"] == 1


def test_master_loop_stops_honestly_when_database_is_missing(tmp_path: Path) -> None:
    output = tmp_path / "master"

    result = run_master_loop(
        root=Path.cwd(),
        database=tmp_path / "missing.db",
        output=output,
    )

    assert result["status"] == TerminalStatus.BLOCKED_DATA_INTEGRITY.value
    assert (output / "BASELINE.json").is_file()
    assert (output / "DATA_INTEGRITY.json").is_file()
    assert not (output / "GENERATION_0.json").exists()
