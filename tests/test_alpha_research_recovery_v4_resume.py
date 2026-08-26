from scripts.run_alpha_research_recovery_v4_resume import _epoch_datetime, _source_summary, baseline_lock


def test_v4_resume_baseline_lock_matches_281_champion_scope() -> None:
    result = baseline_lock()
    lock = result["lock"]

    assert lock["status"] == "BASELINE_LOCKED"
    assert lock["metrics"]["trades"] == 281
    assert lock["metrics"]["btc_trades"] == 132
    assert lock["metrics"]["eth_trades"] == 149
    assert lock["deltas"]["profit_factor"] <= 1e-6
    assert lock["deltas"]["expectancy"] <= 1e-9
    assert lock["deltas"]["max_drawdown"] <= 1e-6
    assert lock["final_holdout_accessed"] is False
    assert all(lock["hashes"].values())


def test_binance_epoch_parser_supports_milliseconds_and_microseconds() -> None:
    assert _epoch_datetime("1704067200000").isoformat() == "2024-01-01T00:00:00+00:00"
    assert _epoch_datetime("1735689600000000").isoformat() == "2025-01-01T00:00:00+00:00"


def test_overlay_research_gate_requires_monotonic_relationship() -> None:
    rows = [
        {"net_r": value, "direction": "long", "breadth": feature}
        for feature, value in ((0.1, -0.01), (0.2, 0.02), (0.3, 0.03), (0.4, -0.02))
    ]
    summary = _source_summary(rows, source="test", feature="breadth")
    assert summary["monotonic_relationship"] is False
    assert summary["research_gate"] == "FAIL"
    assert summary["validation"] == "NOT_RUN"
