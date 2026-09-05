from __future__ import annotations

import json
from pathlib import Path

from scripts.verify_current_state_evidence import verify_current_state_evidence


def _manifest() -> dict[str, object]:
    return {
        "schema_version": 4,
        "strategy_id": "strategy-v4",
        "rules_hash": "a" * 64,
        "strategy_code_hash": "b" * 64,
        "strategy_package_hash": "c" * 64,
    }


def _state(manifest: dict[str, object]) -> str:
    return "\n".join(
        [
            "<!-- BEGIN GENERATED: canonical-strategy-manifest -->",
            f"- Strategy: `{manifest['strategy_id']}` / `1.0.0`",
            f"- Rules Hash: `{manifest['rules_hash']}`",
            f"- Strategy code hash: `{manifest['strategy_code_hash']}`",
            f"- Strategy package hash: `{manifest['strategy_package_hash']}`",
            "<!-- END GENERATED: canonical-strategy-manifest -->",
        ]
    )


def test_current_state_matches_manifest_v4_and_one_click_launcher(tmp_path: Path) -> None:
    manifest = _manifest()
    manifest_path = tmp_path / "manifest.json"
    state_path = tmp_path / "CURRENT_STATE.md"
    launcher_path = tmp_path / "一键启动.cmd"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    state_path.write_text(_state(manifest), encoding="utf-8")
    launcher_path.write_text("launch-paper-console.ps1 -EnableNaturalTestnet", encoding="utf-8")

    assert verify_current_state_evidence(manifest_path, state_path, launcher_path) == []


def test_current_state_rejects_identity_drift_and_stale_launcher_claim(tmp_path: Path) -> None:
    manifest = _manifest()
    manifest_path = tmp_path / "manifest.json"
    state_path = tmp_path / "CURRENT_STATE.md"
    launcher_path = tmp_path / "一键启动.cmd"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    state_path.write_text(
        _state({**manifest, "strategy_package_hash": "d" * 64})
        + "\nThe normal desktop launcher does not arm the Testnet Canary.",
        encoding="utf-8",
    )
    launcher_path.write_text("launch-paper-console.ps1", encoding="utf-8")

    failures = verify_current_state_evidence(manifest_path, state_path, launcher_path)

    assert "CURRENT_STATE strategy_package_hash does not match canonical manifest" in failures
    assert "一键启动.cmd does not include -EnableNaturalTestnet" in failures
    assert "CURRENT_STATE contains stale launcher Canary claim" in failures
