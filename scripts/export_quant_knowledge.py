"""Export video Agent Corpus knowledge as quant research artifacts."""

from __future__ import annotations

import argparse
from pathlib import Path

from services.research.quant_knowledge import export_quant_knowledge


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-root", required=True, type=Path)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("artifacts/strategy_research/export"),
    )
    parser.add_argument("--min-support", type=int, default=1)
    args = parser.parse_args()
    bundle = export_quant_knowledge(
        args.corpus_root,
        args.output_dir,
        min_support_for_hypothesis=args.min_support,
    )
    print(
        f"exported corpus={bundle.corpus_id} primitives={len(bundle.primitives)} "
        f"hypotheses={len(bundle.hypotheses)} proposals={len(bundle.quantization_proposals)} "
        f"hash={bundle.export_hash}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
