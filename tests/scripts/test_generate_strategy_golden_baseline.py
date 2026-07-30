from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from scripts.generate_strategy_golden_baseline import (
    BarCoverage,
    assess_data_coverage,
    generate_golden_baseline,
    latest_common_closed_4h_boundary,
    source_tree_manifest,
    write_immutable_artifacts,
)
from services.data.repository import DataRepository, create_timeseries_schema
from shared.models import Exchange, OHLCVBar, Timeframe

SYMBOLS = ("BTC/USDT", "ETH/USDT")
TIMEFRAMES = ("1m", "5m", "15m", "1h", "4h")


def _coverage(
    *,
    symbol: str,
    timeframe: str,
    first_open: datetime,
    latest_closed_at: datetime,
    gap_count: int = 0,
) -> BarCoverage:
    return BarCoverage(
        symbol=symbol,
        timeframe=timeframe,
        first_open=first_open,
        last_open=latest_closed_at - timedelta(minutes=1),
        latest_closed_at=latest_closed_at,
        bar_count=100,
        gap_count=gap_count,
        missing_bar_count=gap_count,
        largest_gap_seconds=0 if gap_count == 0 else 60,
        data_hash=f"{symbol}:{timeframe}",
    )


def test_latest_common_boundary_uses_slowest_closed_series_and_floors_to_4h() -> None:
    first_open = datetime(2023, 1, 1, tzinfo=UTC)
    coverages = [
        _coverage(
            symbol=symbol,
            timeframe=timeframe,
            first_open=first_open,
            latest_closed_at=datetime(2026, 7, 29, 16, 0, tzinfo=UTC),
        )
        for symbol in SYMBOLS
        for timeframe in TIMEFRAMES
    ]
    slowest = coverages[-1]
    coverages[-1] = _coverage(
        symbol=slowest.symbol,
        timeframe=slowest.timeframe,
        first_open=first_open,
        latest_closed_at=datetime(2026, 7, 29, 13, 7, tzinfo=UTC),
    )

    assert latest_common_closed_4h_boundary(coverages) == datetime(
        2026,
        7,
        29,
        12,
        0,
        tzinfo=UTC,
    )


def test_coverage_gate_requires_42_calendar_months_and_no_series_gaps() -> None:
    cutoff = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)
    coverages = [
        _coverage(
            symbol=symbol,
            timeframe=timeframe,
            first_open=datetime(2023, 1, 1, tzinfo=UTC),
            latest_closed_at=cutoff,
        )
        for symbol in SYMBOLS
        for timeframe in TIMEFRAMES
    ]

    passed = assess_data_coverage(coverages, cutoff=cutoff)

    assert passed["status"] == "SUFFICIENT"
    assert passed["final_holdout_start"] == "2026-01-29T12:00:00+00:00"
    assert passed["required_history_start"] == "2023-01-29T12:00:00+00:00"
    assert passed["required_total_months"] == 42

    coverages[0] = _coverage(
        symbol="BTC/USDT",
        timeframe="1m",
        first_open=datetime(2023, 2, 1, tzinfo=UTC),
        latest_closed_at=cutoff,
        gap_count=1,
    )
    failed = assess_data_coverage(coverages, cutoff=cutoff)

    assert failed["status"] == "DATA_COVERAGE_INSUFFICIENT"
    assert failed["series"]["BTC/USDT|1m"]["starts_before_required_history"] is False
    assert failed["series"]["BTC/USDT|1m"]["continuous"] is False


def test_source_tree_hash_depends_on_sorted_paths_and_file_contents(tmp_path: Path) -> None:
    (tmp_path / "services").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "services" / "b.py").write_text("b = 2\n", encoding="utf-8")
    (tmp_path / "tests" / "a.py").write_text("a = 1\n", encoding="utf-8")

    first = source_tree_manifest(tmp_path)
    second = source_tree_manifest(tmp_path)

    assert first == second
    assert [item["path"] for item in first["files"]] == [
        "services/b.py",
        "tests/a.py",
    ]

    (tmp_path / "services" / "b.py").write_text("b = 3\n", encoding="utf-8")
    changed = source_tree_manifest(tmp_path)

    assert changed["source_tree_hash"] != first["source_tree_hash"]


def test_source_tree_hash_covers_docs_and_root_source_files(tmp_path: Path) -> None:
    (tmp_path / "docs" / "audits").mkdir(parents=True)
    (tmp_path / "docs" / "audits" / "strategy.md").write_text(
        "audit-v1\n",
        encoding="utf-8",
    )
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "scheduler-state.json").write_text(
        '{"runtime":"mutable"}\n',
        encoding="utf-8",
    )
    (tmp_path / "strategy-design.md").write_text("design-v1\n", encoding="utf-8")

    first = source_tree_manifest(tmp_path)
    paths = [item["path"] for item in first["files"]]

    assert paths == [
        "docs/audits/strategy.md",
        "strategy-design.md",
    ]
    assert first["scope"] == "all source/config/document files under source_root"
    assert "artifacts" in first["excluded_path_parts"]
    assert "logs" in first["excluded_path_parts"]

    (tmp_path / "docs" / "audits" / "strategy.md").write_text(
        "audit-v2\n",
        encoding="utf-8",
    )
    changed = source_tree_manifest(tmp_path)

    assert changed["source_tree_hash"] != first["source_tree_hash"]


def test_immutable_writer_refuses_to_overwrite_existing_baseline(tmp_path: Path) -> None:
    destination = tmp_path / "baseline"
    write_immutable_artifacts(
        destination,
        {
            "BASELINE_MANIFEST.json": b'{"status":"DATA_COVERAGE_INSUFFICIENT"}\n',
            "trades.jsonl": b"",
        },
    )

    with pytest.raises(FileExistsError, match="immutable baseline already exists"):
        write_immutable_artifacts(
            destination,
            {"BASELINE_MANIFEST.json": b'{"status":"DIFFERENT"}\n'},
        )


def test_sparse_database_freezes_insufficient_baseline_without_reading_holdout_results(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "market.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    engine = create_engine(database_url)
    create_timeseries_schema(engine)
    observed_at = datetime(2026, 7, 29, 18, 0, tzinfo=UTC)
    timeframe_delta = {
        "1m": timedelta(minutes=1),
        "5m": timedelta(minutes=5),
        "15m": timedelta(minutes=15),
        "1h": timedelta(hours=1),
        "4h": timedelta(hours=4),
    }
    with Session(engine) as session:
        repository = DataRepository(session)
        for symbol in SYMBOLS:
            for timeframe in TIMEFRAMES:
                delta = timeframe_delta[timeframe]
                close_at = datetime(2026, 7, 29, 16, 0, tzinfo=UTC)
                repository.store_ohlcv_bars(
                    [
                        OHLCVBar(
                            symbol=symbol,
                            exchange=Exchange.BINANCE,
                            timeframe=Timeframe(timeframe),
                            time=close_at - delta,
                            open=Decimal("100"),
                            high=Decimal("101"),
                            low=Decimal("99"),
                            close=Decimal("100"),
                            volume=Decimal("10"),
                        )
                    ]
                )

    source_root = tmp_path / "source"
    (source_root / "services").mkdir(parents=True)
    (source_root / "services" / "example.py").write_text("VALUE = 1\n", encoding="utf-8")
    manifest_dir = source_root / "docs" / "evidence" / "active-manifests"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "auto_paper_mature_templates.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "strategy_key": "auto_paper_mature_templates",
                "candidate_id": "trend_momentum_v1",
                "rules_hash": "legacy-rules-hash",
                "eligible_symbols": list(SYMBOLS),
            }
        ),
        encoding="utf-8",
    )
    destination = tmp_path / "artifacts" / "strategy_refactor" / "baseline"

    result = generate_golden_baseline(
        database_url=database_url,
        output_dir=destination,
        source_root=source_root,
        observed_at=observed_at,
        auxiliary_git_sha="test-sha",
    )

    assert result["status"] == "DATA_COVERAGE_INSUFFICIENT"
    assert result["cutoff"] == "2026-07-29T16:00:00+00:00"
    assert result["holdout_results_accessed"] is False
    assert (destination / "trades.jsonl").read_text(encoding="utf-8") == ""
    metrics = json.loads((destination / "metrics.json").read_text(encoding="utf-8"))
    assert metrics == {
        "status": "UNAVAILABLE",
        "reason": "DATA_COVERAGE_INSUFFICIENT",
        "BTC/USDT": None,
        "ETH/USDT": None,
        "portfolio": None,
    }
    frozen_manifest = json.loads((destination / "BASELINE_MANIFEST.json").read_text(encoding="utf-8"))
    assert frozen_manifest["auxiliary_git_sha"] == "test-sha"
    assert frozen_manifest["source_tree_hash"]
    assert frozen_manifest["active_strategy"]["candidate_id"] == "trend_momentum_v1"


def test_generation_does_not_invent_common_cutoff_when_required_series_is_missing(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "empty.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    engine = create_engine(database_url)
    create_timeseries_schema(engine)
    engine.dispose()
    source_root = tmp_path / "source"
    (source_root / "services").mkdir(parents=True)
    (source_root / "services" / "example.py").write_text("VALUE = 1\n", encoding="utf-8")
    manifest_dir = source_root / "docs" / "evidence" / "active-manifests"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "auto_paper_mature_templates.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "strategy_key": "auto_paper_mature_templates",
                "candidate_id": "trend_momentum_v1",
                "rules_hash": "legacy-rules-hash",
                "eligible_symbols": list(SYMBOLS),
            }
        ),
        encoding="utf-8",
    )

    result = generate_golden_baseline(
        database_url=database_url,
        output_dir=tmp_path / "baseline",
        source_root=source_root,
        observed_at=datetime(2026, 7, 29, 18, 0, tzinfo=UTC),
        auxiliary_git_sha="test-sha",
    )

    assert result["status"] == "DATA_COVERAGE_INSUFFICIENT"
    assert result["cutoff"] is None
    assert result["final_holdout_start"] is None
    assert result["holdout_policy"] == "not_frozen; required series unavailable"
    data_manifest = json.loads((tmp_path / "baseline" / "data_manifest.json").read_text(encoding="utf-8"))
    assert data_manifest["coverage_gate"]["series"]["BTC/USDT|5m"]["continuous"] is False
