"""Fail CI when the committed active manifest and CURRENT_STATE Evidence diverge."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

CANONICAL_BEGIN = "<!-- BEGIN GENERATED: canonical-strategy-manifest -->"
CANONICAL_END = "<!-- END GENERATED: canonical-strategy-manifest -->"
_STRATEGY_RE = re.compile(r"- Strategy: `([^`]+)` / `[^`]+`")
_HASH_PATTERNS = {
    "rules_hash": re.compile(r"- Rules Hash: `([^`]+)`"),
    "strategy_code_hash": re.compile(r"- Strategy code hash: `([^`]+)`"),
    "strategy_package_hash": re.compile(r"- Strategy package hash: `([^`]+)`"),
}
STALE_LAUNCHER_CLAIM = "normal desktop launcher does not arm the testnet canary"


def verify_current_state_evidence(manifest_path: Path, state_path: Path, launcher_path: Path) -> list[str]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    state = state_path.read_text(encoding="utf-8")
    launcher = launcher_path.read_text(encoding="utf-8")
    failures: list[str] = []
    if manifest.get("schema_version") != 4:
        failures.append("manifest schema_version must be 4")
        return failures
    begin = state.find(CANONICAL_BEGIN)
    end = state.find(CANONICAL_END)
    block = state[begin:end] if begin >= 0 and end > begin else ""
    strategy_match = _STRATEGY_RE.search(block)
    if not strategy_match or strategy_match.group(1) != str(manifest.get("strategy_id")):
        failures.append("CURRENT_STATE strategy_id does not match canonical manifest")
    for field, pattern in _HASH_PATTERNS.items():
        match = pattern.search(block)
        if not match or match.group(1) != str(manifest.get(field)):
            failures.append(f"CURRENT_STATE {field} does not match canonical manifest")
    if "-EnableNaturalTestnet" not in launcher:
        failures.append("一键启动.cmd does not include -EnableNaturalTestnet")
    if STALE_LAUNCHER_CLAIM in state.lower():
        failures.append("CURRENT_STATE contains stale launcher Canary claim")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("docs/evidence/active-manifests/auto_paper_mature_templates.json"),
    )
    parser.add_argument("--state", type=Path, default=Path("CURRENT_STATE.md"))
    parser.add_argument("--launcher", type=Path, default=Path("一键启动.cmd"))
    args = parser.parse_args()
    failures = verify_current_state_evidence(args.manifest, args.state, args.launcher)
    if failures:
        raise SystemExit("CURRENT_STATE_EVIDENCE_MISMATCH\n" + "\n".join(f"FAIL: {item}" for item in failures))
    print(f"CURRENT_STATE evidence matches {args.manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
