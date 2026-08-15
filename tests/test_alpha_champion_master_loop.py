from __future__ import annotations

import sqlite3
from pathlib import Path

from scripts.run_alpha_champion_master_loop import (
    PROPOSAL_CANDIDATES,
    TerminalStatus,
    _generation_one_specs,
    _generation_two_specs,
    audit_market_data,
    bounded_search_plan,
    discover_candidate_inventory,
    run_master_loop,
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
    assert all(not records[candidate_id].execution_eligible for candidate_id in PROPOSAL_CANDIDATES)


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
