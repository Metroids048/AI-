"""CLI wrapper for the research-only vectorbt adapter."""

from __future__ import annotations

import argparse
import json

from services.research.integrations.contracts import ResearchExperimentSpec
from services.research.integrations.dataset_export import load_canonical_dataset
from services.research.integrations.vectorbt_adapter import VectorbtScreenAdapter


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--spec", required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    dataset = load_canonical_dataset(args.dataset)
    spec = ResearchExperimentSpec(**json.loads(open(args.spec, encoding="utf-8").read()))
    result = VectorbtScreenAdapter().screen(spec, dataset.get("rows", []), run_id=args.run_id)
    print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
