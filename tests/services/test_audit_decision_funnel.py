from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

import scripts.audit_decision_funnel as audit_module
from scripts.audit_decision_funnel import run_audit


def test_funnel_audit_filters_strategy_and_counts_pipeline_and_gatekeeper_stages(tmp_path) -> None:
    path = tmp_path / "funnel.db"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE strategies (id TEXT PRIMARY KEY, strategy_key TEXT NOT NULL);
        CREATE TABLE paper_runs (paper_run_id TEXT PRIMARY KEY, strategy_id TEXT NOT NULL);
        CREATE TABLE decision_snapshots (
            paper_run_id TEXT, symbol TEXT, action TEXT, pipeline_status TEXT, reason TEXT,
            decision_trace TEXT, cycle_time DATETIME
        );
        CREATE TABLE order_executions (
            paper_run_id TEXT, execution_status TEXT, rejection_codes TEXT, created_at DATETIME
        );
        """
    )
    now = datetime.now(UTC).replace(tzinfo=None)
    old = now - timedelta(days=10)
    connection.executemany("INSERT INTO strategies VALUES (?, ?)", [("s1", "main"), ("s2", "other")])
    connection.executemany("INSERT INTO paper_runs VALUES (?, ?)", [("p1", "s1"), ("p2", "s2")])
    connection.executemany(
        "INSERT INTO decision_snapshots VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (
                "p1",
                "BTC/USDT",
                "skip_no_trade_decision",
                "technical_signals_insufficient",
                "technical_signals_insufficient",
                "{}",
                now.isoformat(sep=" "),
            ),
            (
                "p1",
                "ETH/USDT",
                "skip_no_trade_decision",
                "multi_timeframe_disagreement",
                "multi_timeframe_disagreement",
                "{}",
                now.isoformat(sep=" "),
            ),
            (
                "p1",
                "BTC/USDT",
                "rejected",
                "bet_taken",
                "validated_edge_stats_missing_or_stale",
                json.dumps({"edge_stats_source": "validated_edge_stats_missing_or_stale"}),
                now.isoformat(sep=" "),
            ),
            ("p1", "BTC/USDT", "open_long", "bet_taken", "accepted", "{}", now.isoformat(sep=" ")),
            (
                "p1",
                "BTC/USDT",
                "skip_no_trade_decision",
                "ensemble_discarded",
                "ensemble_discarded",
                "{}",
                old.isoformat(sep=" "),
            ),
            ("p2", "ETH/USDT", "open_long", "bet_taken", "accepted", "{}", now.isoformat(sep=" ")),
        ],
    )
    connection.execute(
        "INSERT INTO order_executions VALUES (?, ?, ?, ?)",
        (
            "p1",
            "rejected",
            json.dumps(["validated_edge_stats_missing_or_stale", "correlated_exposure_limit_exceeded"]),
            now.isoformat(sep=" "),
        ),
    )
    connection.commit()
    connection.close()

    report = run_audit(
        database_url=f"sqlite:///{path.as_posix()}",
        since=datetime.now(UTC) - timedelta(days=1),
        strategy_key="main",
    )

    assert report.total_decisions == 4
    assert report.stage_counts == {
        "technical_signals": 1,
        "multi_timeframe": 1,
        "validated_edge": 1,
        "opened": 1,
    }
    assert report.rejection_code_counts == {
        "correlated_exposure_limit_exceeded": 1,
        "validated_edge_stats_missing_or_stale": 1,
    }


def test_funnel_audit_reports_sequential_reach_and_blockers(tmp_path) -> None:
    path = tmp_path / "sequential-funnel.db"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE strategies (id TEXT PRIMARY KEY, strategy_key TEXT NOT NULL);
        CREATE TABLE paper_runs (paper_run_id TEXT PRIMARY KEY, strategy_id TEXT NOT NULL);
        CREATE TABLE decision_snapshots (
            paper_run_id TEXT, symbol TEXT, action TEXT, pipeline_status TEXT,
            reason TEXT, decision_trace TEXT, cycle_time DATETIME
        );
        CREATE TABLE order_executions (
            paper_run_id TEXT, execution_status TEXT, rejection_codes TEXT, created_at DATETIME
        );
        """
    )
    now = datetime.now(UTC).replace(tzinfo=None)
    connection.execute("INSERT INTO strategies VALUES (?, ?)", ("s1", "main"))
    connection.execute("INSERT INTO paper_runs VALUES (?, ?)", ("p1", "s1"))

    def at(minutes: int) -> str:
        return (now + timedelta(minutes=minutes)).isoformat(sep=" ")

    def trace(
        *,
        signals: list[dict[str, object]],
        regime: str = "trending",
        mtf_status: str | None = "confirmed",
        ensemble_status: str | None = "passed_to_meta_label",
        fused_direction: str | None = "long",
        veto: bool | None = False,
    ) -> str:
        return json.dumps(
            {
                "strategy_lane": "directional",
                "signals": signals,
                "ensemble": None
                if ensemble_status is None
                else {
                    "ensemble_status": ensemble_status,
                    "fused_direction": fused_direction,
                },
                "meta_label": {"bet_decision": "bet_taken"},
                "veto_result": None if veto is None else {"veto": veto},
                "volatility": {
                    "regime": regime,
                    "multi_timeframe": None if mtf_status is None else {"status": mtf_status},
                },
            }
        )

    long_signal = [{"side": "long", "source": "macd"}]
    short_signal = [{"side": "short", "source": "price_action"}]
    rows = [
        (
            "p1",
            "BTC/USDT",
            "skip_no_trade_decision",
            "technical_signals_insufficient",
            "technical_signals_insufficient",
            trace(signals=[], mtf_status=None, ensemble_status=None, veto=None),
            at(0),
        ),
        (
            "p1",
            "ETH/USDT",
            "skip_no_trade_decision",
            "multi_timeframe_disagreement",
            "multi_timeframe_disagreement",
            trace(signals=long_signal, mtf_status="state_confirmation_disagreed", ensemble_status=None, veto=None),
            at(1),
        ),
        (
            "p1",
            "BTC/USDT",
            "hold_long",
            "ensemble_discarded",
            "ensemble_discarded",
            trace(
                signals=short_signal,
                ensemble_status="discarded_low_confidence",
                fused_direction=None,
                veto=None,
            ),
            at(2),
        ),
        (
            "p1",
            "ETH/USDT",
            "skip_no_trade_decision",
            "meta_label_bet_skipped",
            "meta_label_bet_skipped",
            trace(signals=long_signal, veto=None),
            at(3),
        ),
        (
            "p1",
            "SOL/USDT",
            "skip_no_trade_decision",
            "vetoed",
            "llm_veto",
            trace(signals=long_signal, veto=True),
            at(4),
        ),
        (
            "p1",
            "SOL/USDT",
            "rejected",
            "bet_taken",
            "net_directional_exposure_exceeded",
            trace(signals=long_signal),
            at(5),
        ),
        (
            "p1",
            "BTC/USDT",
            "open_long",
            "bet_taken",
            "accepted",
            trace(signals=long_signal),
            at(6),
        ),
    ]
    connection.executemany("INSERT INTO decision_snapshots VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    connection.execute(
        "INSERT INTO order_executions VALUES (?, ?, ?, ?)",
        ("p1", "rejected", json.dumps(["net_directional_exposure_exceeded"]), at(5)),
    )
    connection.commit()
    connection.close()

    report = run_audit(
        database_url=f"sqlite:///{path.as_posix()}",
        since=datetime.now(UTC) - timedelta(days=1),
        strategy_key="main",
    )

    assert report.metrics == {
        "cycles": 7,
        "symbols_evaluated": 7,
        "raw_long_signals": 5,
        "raw_short_signals": 1,
        "no_base_signal": 1,
        "mtf_evaluated": 6,
        "mtf_pass": 5,
        "mtf_disagreement": 1,
        "ensemble_evaluated": 5,
        "ensemble_pass": 4,
        "ensemble_discard": 1,
        "meta_label_evaluated": 4,
        "meta_label_pass": 3,
        "meta_label_skip": 1,
        "llm_evaluated": 3,
        "llm_pass": 2,
        "llm_veto": 1,
        "risk_evaluated": 2,
        "risk_pass": 1,
        "risk_block": 1,
        "trade_intents": 1,
    }
    assert [row.stage for row in report.funnel_rows] == [
        "base_signal",
        "multi_timeframe",
        "ensemble",
        "meta_label",
        "llm_veto",
        "gatekeeper",
        "trade_intent",
    ]
    assert [(row.entered, row.passed, row.eliminated) for row in report.funnel_rows] == [
        (7, 6, 1),
        (6, 5, 1),
        (5, 4, 1),
        (4, 3, 1),
        (3, 2, 1),
        (2, 1, 1),
        (1, 1, 0),
    ]
    assert report.blocker_counts == {
        "ENSEMBLE_DISCARD": 1,
        "LLM_VETO": 1,
        "META_LABEL_SKIP": 1,
        "MTF_DISAGREEMENT": 1,
        "NO_BASE_SIGNAL": 1,
        "RISK_BLOCK": 1,
    }
    assert report.blocker_breakdowns["symbol"]["BTC/USDT"]["NO_BASE_SIGNAL"] == 1
    assert report.blocker_breakdowns["regime"]["trending"]["LLM_VETO"] == 1
    assert report.blocker_breakdowns["direction"]["long"]["RISK_BLOCK"] == 1


def test_funnel_audit_writes_review_layer_artifacts(tmp_path) -> None:
    report = audit_module.FunnelAuditReport(
        generated_at="2026-07-22T00:00:00+00:00",
        since="2026-07-21T00:00:00+00:00",
        strategy_key=None,
        total_decisions=2,
        stage_counts={"ensemble": 1, "opened": 1},
        stage_percentages={"ensemble": 50.0, "opened": 50.0},
        pipeline_status_counts={"bet_taken": 1, "ensemble_discarded": 1},
        rejection_code_counts={},
        metrics={"cycles": 1, "symbols_evaluated": 2, "trade_intents": 1},
        funnel_rows=[
            audit_module.FunnelStageRow(
                stage="ensemble",
                entered=2,
                passed=1,
                eliminated=1,
                elimination_rate_percent=50.0,
            )
        ],
        blocker_counts={"ENSEMBLE_DISCARD": 1},
        blocker_breakdowns={
            "symbol": {"BTC/USDT": {"ENSEMBLE_DISCARD": 1}},
            "hour_utc": {"2026-07-22T00:00Z": {"ENSEMBLE_DISCARD": 1}},
            "regime": {"low_volatility": {"ENSEMBLE_DISCARD": 1}},
            "direction": {"short": {"ENSEMBLE_DISCARD": 1}},
        },
    )
    funnel_path = tmp_path / "strategy-decision-funnel.csv"
    blocker_path = tmp_path / "blocker-distribution.csv"
    report_path = tmp_path / "strategy-liveness-funnel.md"

    audit_module.write_artifacts(
        report,
        funnel_csv_path=funnel_path,
        blocker_csv_path=blocker_path,
        markdown_path=report_path,
    )

    assert funnel_path.read_text(encoding="utf-8").splitlines() == [
        "stage,entered,passed,eliminated,elimination_rate_percent",
        "ensemble,2,1,1,50.0",
    ]
    blocker_text = blocker_path.read_text(encoding="utf-8")
    assert "dimension,dimension_value,blocker,count,percent_of_dimension" in blocker_text
    assert "symbol,BTC/USDT,ENSEMBLE_DISCARD,1,100.0" in blocker_text
    markdown = report_path.read_text(encoding="utf-8")
    assert "# Strategy Liveness Funnel" in markdown
    assert "| ensemble | 2 | 1 | 1 | 50.00% |" in markdown
    assert "| ENSEMBLE_DISCARD | 1 |" in markdown
