from __future__ import annotations

import importlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path


def test_strategy_ablation_audit_compares_five_variants_and_writes_evidence(tmp_path: Path) -> None:
    module_path = Path("scripts/audit_strategy_ablation.py")
    assert module_path.is_file(), "read-only strategy ablation audit is not implemented"
    audit_module = importlib.import_module("scripts.audit_strategy_ablation")
    database = tmp_path / "ablation.db"
    connection = sqlite3.connect(database)
    connection.execute(
        "CREATE TABLE decision_snapshots (paper_run_id TEXT, symbol TEXT, pipeline_status TEXT, "
        "decision_trace TEXT, cycle_time DATETIME)"
    )
    now = datetime.now(UTC).replace(tzinfo=None).isoformat(sep=" ")
    connection.executemany(
        "INSERT INTO decision_snapshots VALUES ('run-1', ?, ?, ?, ?)",
        [
            (
                "BTC/USDT",
                "ensemble_discarded",
                json.dumps(
                    {
                        "pipeline_status": "ensemble_discarded",
                        "signals": [
                            {"side": "long", "confidence": 0.8},
                            {"side": "short", "confidence": 0.2},
                        ],
                        "ensemble": {"fused_direction": None},
                        "volatility": {"multi_timeframe": {"passed": True, "status": "confirmed"}},
                    }
                ),
                now,
            ),
            (
                "ETH/USDT",
                "vetoed",
                json.dumps(
                    {
                        "pipeline_status": "vetoed",
                        "signals": [{"side": "short", "confidence": 0.7}],
                        "ensemble": {"fused_direction": "short"},
                        "veto_result": {"veto": True},
                        "volatility": {"multi_timeframe": {"passed": True, "status": "confirmed"}},
                    }
                ),
                now,
            ),
            (
                "BTC/USDT",
                "funding_arbitrage_rejected",
                json.dumps(
                    {
                        "pipeline_status": "funding_arbitrage_rejected",
                        "strategy_lane": "cross_sectional_carry",
                        "rejection_reasons": ["net_edge_after_cost_negative"],
                    }
                ),
                now,
            ),
        ],
    )
    connection.commit()
    connection.close()

    report = audit_module.run_audit(
        database,
        since=datetime.now(UTC) - timedelta(days=1),
    )

    assert report.decision_count == 2
    assert len(report.results) == 10
    summaries = {item.variant: item for item in report.variant_summaries}
    assert summaries["A_CURRENT_PRODUCTION"].candidate_count == 0
    assert summaries["B_NO_LLM_HARD_VETO"].candidate_count == 1
    assert summaries["C_WEIGHTED_ENSEMBLE"].candidate_count == 1
    assert summaries["D_HIERARCHICAL_MTF"].candidate_count == 0
    assert summaries["E_COMBINED_BCD"].candidate_count == 2

    csv_path = tmp_path / "shadow-ablation-results.csv"
    markdown_path = tmp_path / "shadow-ablation-report.md"
    audit_module.write_artifacts(report, csv_path=csv_path, markdown_path=markdown_path)
    assert len(csv_path.read_text(encoding="utf-8").splitlines()) == 11
    markdown = markdown_path.read_text(encoding="utf-8")
    assert "# Strategy Shadow Ablation" in markdown
    assert "A_CURRENT_PRODUCTION" in markdown
    assert "candidate recall only" in markdown
