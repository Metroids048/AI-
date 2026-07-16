from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from services.execution.signal_edge_stats import load_active_edge_stats, strategy_rules_hash

RULES = {
    "entry_rules": {"entry_signals": ["macd"], "core_fee_bps": 5.0},
    "takeprofit_rules": {"risk_reward": 2.0},
}


def _artifact(**overrides) -> dict:
    payload = {
        "schema_version": 2,
        "strategy_key": "s1",
        "candidate_id": "candidate",
        "rules_hash": strategy_rules_hash(RULES),
        "symbol": "BTC/USDT",
        "computed_at": datetime.now(UTC).isoformat(),
        "sample_count": 120,
        "oos_sample_count": 40,
        "win_rate": 0.42,
        "average_net_win": 0.015,
        "average_net_loss_magnitude": 0.011,
        "net_expectancy": 0.0012,
        "sharpe": 1.4,
        "profit_factor": 1.5,
        "max_drawdown": 0.18,
        "evaluation_start": "2026-05-01T00:00:00+00:00",
        "evaluation_end": "2026-07-01T00:00:00+00:00",
        "eligible": True,
        "failed_reasons": [],
        "max_age_days": 30,
    }
    payload.update(overrides)
    return payload


def _write(tmp_path, payload: dict) -> None:
    target = tmp_path / "s1" / "candidate" / "BTCUSDT"
    target.mkdir(parents=True)
    (target / "active.json").write_text(json.dumps(payload), encoding="utf-8")


class TestLoadActiveEdgeStats:
    def test_returns_none_when_pointer_file_is_absent(self, tmp_path, monkeypatch) -> None:
        import services.execution.signal_edge_stats as module

        monkeypatch.setattr(module, "EDGE_STATS_ARTIFACT_DIR", tmp_path)
        assert load_active_edge_stats("s1", "candidate", "BTC/USDT", RULES) is None

    def test_returns_none_when_artifact_older_than_max_age_days(self, tmp_path, monkeypatch) -> None:
        import services.execution.signal_edge_stats as module

        monkeypatch.setattr(module, "EDGE_STATS_ARTIFACT_DIR", tmp_path)
        _write(tmp_path, _artifact(computed_at=(datetime.now(UTC) - timedelta(days=45)).isoformat()))
        assert load_active_edge_stats(
            "s1", "candidate", "BTC/USDT", RULES, now=datetime.now(UTC)
        ) is None

    def test_returns_artifact_when_pointer_valid_fresh_and_eligible(self, tmp_path, monkeypatch) -> None:
        import services.execution.signal_edge_stats as module

        monkeypatch.setattr(module, "EDGE_STATS_ARTIFACT_DIR", tmp_path)
        _write(tmp_path, _artifact())

        artifact = load_active_edge_stats("s1", "candidate", "BTC/USDT", RULES)

        assert artifact is not None
        assert artifact.sample_count == 120
        assert artifact.oos_sample_count == 40
        assert artifact.average_net_loss_magnitude == 0.011
        assert artifact.net_expectancy == 0.0012

    def test_returns_none_for_malformed_ineligible_or_incomplete_artifact(self, tmp_path, monkeypatch) -> None:
        import services.execution.signal_edge_stats as module

        monkeypatch.setattr(module, "EDGE_STATS_ARTIFACT_DIR", tmp_path)
        target = tmp_path / "s1" / "candidate" / "BTCUSDT"
        target.mkdir(parents=True)
        pointer = target / "active.json"
        pointer.write_text("{not valid json", encoding="utf-8")
        assert load_active_edge_stats("s1", "candidate", "BTC/USDT", RULES) is None

        pointer.write_text(json.dumps(_artifact(eligible=False)), encoding="utf-8")
        assert load_active_edge_stats("s1", "candidate", "BTC/USDT", RULES) is None

        pointer.write_text(json.dumps({"computed_at": datetime.now(UTC).isoformat()}), encoding="utf-8")
        assert load_active_edge_stats("s1", "candidate", "BTC/USDT", RULES) is None

    def test_rejects_rules_hash_mismatch_and_keeps_symbols_isolated(self, tmp_path, monkeypatch) -> None:
        import services.execution.signal_edge_stats as module

        monkeypatch.setattr(module, "EDGE_STATS_ARTIFACT_DIR", tmp_path)
        _write(tmp_path, _artifact())

        assert load_active_edge_stats("s1", "candidate", "ETH/USDT", RULES) is None
        assert load_active_edge_stats("s1", "candidate", "BTC/USDT", {"entry_rules": {}}) is None
