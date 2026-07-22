from __future__ import annotations

import importlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).replace(tzinfo=None).isoformat(sep=" ")


def _seed_manual_trade_ledger(path: Path) -> tuple[datetime, datetime]:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE order_executions (
            order_execution_id TEXT PRIMARY KEY,
            symbol TEXT,
            direction TEXT,
            close_only_mode INTEGER,
            gateway_order_id TEXT,
            gateway_status TEXT,
            entry_context TEXT,
            created_at DATETIME
        );
        CREATE TABLE decision_snapshots (
            paper_run_id TEXT,
            symbol TEXT,
            action TEXT,
            pipeline_status TEXT,
            reason TEXT,
            decision_trace TEXT,
            cycle_time DATETIME
        );
        CREATE TABLE ohlcv_bars (
            time DATETIME,
            symbol TEXT,
            exchange TEXT,
            timeframe TEXT,
            open NUMERIC,
            high NUMERIC,
            low NUMERIC,
            close NUMERIC,
            volume NUMERIC
        );
        """
    )
    first_entry = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)
    second_entry = datetime(2026, 7, 21, 13, 0, tzinfo=UTC)
    connection.executemany(
        "INSERT INTO order_executions VALUES (?, ?, ?, 0, ?, ?, ?, ?)",
        [
            (
                "manual-btc",
                "BTC/USDT",
                "long",
                "gateway-btc",
                "filled",
                json.dumps(
                    {
                        "execution_kind": "binance_demo_reconciliation",
                        "exchange_side": "BUY",
                        "order_type": "LIMIT",
                        "quantity": 1.0,
                        "actual_avg_price": 100.0,
                        "strategy_performance_eligible": False,
                    }
                ),
                _iso(first_entry),
            ),
            (
                "manual-eth",
                "ETH/USDT",
                "short",
                "gateway-eth",
                "new",
                json.dumps(
                    {
                        "execution_kind": "binance_demo_reconciliation",
                        "exchange_side": "SELL",
                        "order_type": "LIMIT",
                        "quantity": 2.0,
                        "actual_avg_price": 0.0,
                        "strategy_performance_eligible": False,
                    }
                ),
                _iso(second_entry),
            ),
        ],
    )
    connection.executemany(
        "INSERT INTO decision_snapshots VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (
                "run-1",
                "BTC/USDT",
                "hold_long",
                "ensemble_discarded",
                "ensemble_discarded",
                json.dumps(
                    {
                        "pipeline_status": "ensemble_discarded",
                        "signals": [{"side": "long", "source": "macd", "confidence": 0.8}],
                        "ensemble": {"ensemble_status": "discarded_low_confidence"},
                        "veto_result": None,
                        "volatility": {
                            "regime": "trending",
                            "multi_timeframe": {"status": "confirmed", "main_direction": "long"},
                        },
                    }
                ),
                _iso(first_entry - timedelta(minutes=5)),
            ),
            (
                "run-1",
                "ETH/USDT",
                "skip_no_trade_decision",
                "multi_timeframe_disagreement",
                "multi_timeframe_disagreement",
                json.dumps(
                    {
                        "pipeline_status": "multi_timeframe_disagreement",
                        "signals": [{"side": "short", "source": "price_action", "confidence": 0.7}],
                        "ensemble": None,
                        "veto_result": None,
                        "volatility": {
                            "regime": "trending",
                            "multi_timeframe": {
                                "status": "state_confirmation_disagreed",
                                "main_direction": "short",
                                "state_direction": "long",
                            },
                        },
                    }
                ),
                _iso(second_entry - timedelta(minutes=5)),
            ),
        ],
    )
    connection.executemany(
        "INSERT INTO ohlcv_bars VALUES (?, 'BTC/USDT', 'binance', '15m', ?, ?, ?, ?, ?)",
        [
            (_iso(first_entry), 100, 102, 99, 101, 10),
            (_iso(first_entry + timedelta(minutes=15)), 101, 104, 98, 103, 12),
        ],
    )
    connection.commit()
    connection.close()
    return first_entry, second_entry


def test_manual_trade_analysis_uses_prior_decision_and_separates_future_outcome(tmp_path: Path) -> None:
    database = tmp_path / "manual-trades.db"
    first_entry, _ = _seed_manual_trade_ledger(database)
    module_path = Path("scripts/audit_manual_trade_misses.py")
    assert module_path.is_file(), "Review-layer manual-trade audit module is not implemented"
    analyze_manual_trades = importlib.import_module("scripts.audit_manual_trade_misses").analyze_manual_trades

    report = analyze_manual_trades(
        database,
        order_execution_ids=("manual-btc", "manual-eth"),
        decision_lookback=timedelta(minutes=90),
        outcome_window=timedelta(hours=24),
    )

    assert report.generated_at.tzinfo is UTC
    assert len(report.trades) == 2
    btc = report.trades[0]
    assert btc.order_execution_id == "manual-btc"
    assert btc.entry_time_utc == first_entry
    assert btc.entry_price == 100.0
    assert btc.pipeline_status == "ensemble_discarded"
    assert btc.final_blocker == "ENSEMBLE_DISCARD"
    assert btc.classification == "ENSEMBLE_FALSE_NEGATIVE"
    assert btc.decision_age_minutes == 5.0
    assert btc.market_regime == "trending"
    assert btc.mtf_status == "confirmed"
    assert btc.mfe_fraction == pytest.approx(0.04)
    assert btc.mae_fraction == pytest.approx(0.02)
    assert btc.theoretical_stop_price is None
    assert btc.reached_1r is None

    eth = report.trades[1]
    assert eth.order_execution_id == "manual-eth"
    assert eth.entry_price is None
    assert eth.final_blocker == "MTF_DISAGREEMENT"
    assert eth.classification == "INSUFFICIENT_EVIDENCE"
    assert eth.mfe_fraction is None
    assert "entry fill price is unavailable" in eth.evidence_gaps


def test_manual_trade_analysis_writes_csv_and_markdown(tmp_path: Path) -> None:
    database = tmp_path / "manual-trades.db"
    _seed_manual_trade_ledger(database)
    audit_module = importlib.import_module("scripts.audit_manual_trade_misses")
    report = audit_module.analyze_manual_trades(
        database,
        order_execution_ids=("manual-btc", "manual-eth"),
    )
    csv_path = tmp_path / "manual-trade-miss-analysis.csv"
    markdown_path = tmp_path / "manual-trade-miss-analysis.md"

    audit_module.write_artifacts(
        report,
        csv_path=csv_path,
        markdown_path=markdown_path,
    )

    csv_text = csv_path.read_text(encoding="utf-8")
    assert "order_execution_id,symbol,side,entry_time_utc,entry_price" in csv_text
    assert "manual-btc,BTC/USDT,long" in csv_text
    assert "ENSEMBLE_FALSE_NEGATIVE" in csv_text
    markdown = markdown_path.read_text(encoding="utf-8")
    assert "# Manual Trade Miss Analysis" in markdown
    assert "## BTC/USDT manual-btc" in markdown
    assert "INSUFFICIENT_EVIDENCE" in markdown


def test_manual_trade_analysis_reads_outcomes_from_separate_market_database(tmp_path: Path) -> None:
    ledger = tmp_path / "runtime-ledger.db"
    _seed_manual_trade_ledger(ledger)
    market = tmp_path / "market-data.db"
    source = sqlite3.connect(ledger)
    bars = source.execute("SELECT * FROM ohlcv_bars").fetchall()
    source.execute("DROP TABLE ohlcv_bars")
    source.commit()
    source.close()
    target = sqlite3.connect(market)
    target.execute(
        "CREATE TABLE ohlcv_bars (time DATETIME, symbol TEXT, exchange TEXT, timeframe TEXT, "
        "open NUMERIC, high NUMERIC, low NUMERIC, close NUMERIC, volume NUMERIC)"
    )
    target.executemany("INSERT INTO ohlcv_bars VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", bars)
    target.commit()
    target.close()
    audit_module = importlib.import_module("scripts.audit_manual_trade_misses")

    report = audit_module.analyze_manual_trades(
        ledger,
        market_database=market,
        order_execution_ids=("manual-btc",),
    )

    assert report.market_database == market.resolve().as_posix()
    assert report.trades[0].mfe_fraction == pytest.approx(0.04)


def test_manual_trade_analysis_does_not_call_matching_blocker_a_false_negative_without_outcome_support(
    tmp_path: Path,
) -> None:
    database = tmp_path / "manual-trades.db"
    _seed_manual_trade_ledger(database)
    connection = sqlite3.connect(database)
    connection.execute("UPDATE ohlcv_bars SET high = 101, low = 95 WHERE symbol = 'BTC/USDT'")
    connection.commit()
    connection.close()
    audit_module = importlib.import_module("scripts.audit_manual_trade_misses")

    report = audit_module.analyze_manual_trades(
        database,
        order_execution_ids=("manual-btc",),
    )

    assert report.trades[0].mfe_fraction == pytest.approx(0.01)
    assert report.trades[0].mae_fraction == pytest.approx(0.05)
    assert report.trades[0].classification == "INSUFFICIENT_EVIDENCE"
