"""Fail CI when the committed active manifest and CURRENT_STATE Evidence diverge."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("docs/evidence/active-manifests/auto_paper_mature_templates.json"),
    )
    parser.add_argument("--state", type=Path, default=Path("CURRENT_STATE.md"))
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    state = args.state.read_text(encoding="utf-8")
    required = [
        str(manifest["candidate_id"]),
        str(manifest["rules_hash"]),
        *(str(symbol) for symbol in manifest["eligible_symbols"]),
        Path(str(manifest["report_path"])).name,
    ]
    missing = [value for value in required if value not in state]
    if missing:
        raise SystemExit("CURRENT_STATE Evidence missing manifest values: " + ", ".join(missing))
    print(f"CURRENT_STATE evidence matches {args.manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
