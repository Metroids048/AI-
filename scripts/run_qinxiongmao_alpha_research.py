"""Run the research-only QINXIONGMAO alpha pipeline on historical OHLCV."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from services.research.quant_knowledge.runner import run_alpha_research


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=".strategy_refactor_history.db", type=Path)
    parser.add_argument(
        "--bundle", default="artifacts/strategy_research/export/quant_knowledge_bundle.jsonl", type=Path
    )
    parser.add_argument("--output-dir", default="artifacts/strategy_research/qinxiongmao_alpha", type=Path)
    parser.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()
    progress = run_alpha_research(
        db_path=args.db,
        bundle_path=args.bundle,
        output_dir=args.output_dir,
        resume=args.resume,
    )
    print(
        json.dumps(
            {
                "status": progress["status"],
                "registered": progress["hypotheses"]["registered"],
                "terminal": progress["hypotheses"]["terminal"],
                "running": progress["hypotheses"]["running"],
                "unknown": progress["hypotheses"]["unknown"],
                "families": progress["families"],
                "candidate_compositions": progress.get("candidate_compositions", 0),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if progress["status"] == "QINXIONGMAO_KNOWLEDGE_ALPHA_PIPELINE_COMPLETE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
