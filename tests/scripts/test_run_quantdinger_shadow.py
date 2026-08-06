"""Tests for the offline QuantDinger artifact command."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from scripts.run_quantdinger_shadow import build_artifact
from services.validation.quantdinger_differential_replay import parse_quantdinger_replay_artifact
from tests.services.test_quantdinger_shadow_runtime import SOURCE, _bars


def test_build_artifact_runs_worker_and_writes_hash_bound_replay(tmp_path: Path) -> None:
    source_path = tmp_path / "strategy.py"
    bars_path = tmp_path / "bars.json"
    output_path = tmp_path / "artifact.json"
    source_path.write_text(SOURCE, encoding="utf-8")
    bars_path.write_text(json.dumps({"BTC/USDT": _bars()}), encoding="utf-8")

    artifact = build_artifact(
        source_path=source_path,
        bars_path=bars_path,
        output_path=output_path,
        strategy_id="qd-cli-test",
        strategy_version="5.0.1",
    )

    assert output_path.exists()
    assert artifact["schema_version"] == "1"
    assert len(artifact["signals"]) == 1
    assert len(artifact["trades"]) == 1
    assert artifact["signals"][0]["manifest_code_hash"] == artifact["manifest_code_hash"]
    assert Decimal(artifact["signals"][0]["stop_distance"]) == Decimal("651")
    saved = json.loads(output_path.read_text(encoding="utf-8"))
    assert saved["manifest_code_hash"] == artifact["manifest_code_hash"]

    parsed = parse_quantdinger_replay_artifact(
        saved,
        expected_manifest_code_hash=artifact["manifest_code_hash"],
        expected_timeframe=artifact["timeframe"],
        minimum_warmup_bars=artifact["warmup_bars"],
    )
    assert parsed.warmup_bars == artifact["warmup_bars"]
    assert len(parsed.trades) == len(artifact["trades"])
