"""Publish validated local OOS pointers for the committed active manifest."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from services.execution.bootstrap import AUTO_PAPER_TECHNICAL_KEY, CANONICAL_MANIFEST_ROOT
from services.execution.signal_edge_stats import EDGE_STATS_ARTIFACT_DIR, publish_committed_edge_evidence
from services.strategy_library.candidates.registry import get_candidate


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategy-key", default=AUTO_PAPER_TECHNICAL_KEY)
    parser.add_argument("--manifest", type=Path, default=None)
    args = parser.parse_args()
    manifest_path = args.manifest or EDGE_STATS_ARTIFACT_DIR / args.strategy_key / "active-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("strategy_key") != args.strategy_key:
        raise ValueError("active manifest strategy_key mismatch")
    candidate_id = manifest["candidate_id"]
    published = publish_committed_edge_evidence(
        manifest_path=manifest_path,
        rules=get_candidate(str(candidate_id)).get_config(),
    )
    canonical_manifest = CANONICAL_MANIFEST_ROOT / f"{args.strategy_key}.json"
    canonical_manifest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(manifest_path, canonical_manifest)
    for path in published:
        print(path.as_posix())
    print(canonical_manifest.as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
