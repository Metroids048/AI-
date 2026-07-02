"""Scan the local alpha workspace and turn candidates into StrategyIdea seeds."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from shared.models import StrategyIdea

from .expression_parser import parse_alpha_expression

SUPPORTED_FILES = (
    "alpha_candidates.jsonl",
    "hopeful_alphas.jsonl",
    "alpha_generated_expressions.csv",
)


class LocalAlphaScanner:
    """Read curated alpha artifacts without importing them into execution."""

    def scan(self, root_path: str | Path, *, limit: int = 10) -> list[StrategyIdea]:
        root = Path(root_path)
        if not root.exists():
            raise FileNotFoundError(f"alpha directory not found: {root}")

        ideas: list[StrategyIdea] = []
        seen_expressions: set[str] = set()
        for filename in SUPPORTED_FILES:
            path = root / filename
            if not path.exists():
                continue
            if path.suffix == ".jsonl":
                for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                    if len(ideas) >= limit:
                        return ideas
                    if not line.strip():
                        continue
                    payload = json.loads(line)
                    expression = str(payload.get("expression", "")).strip()
                    if not expression or expression in seen_expressions:
                        continue
                    ideas.append(self._idea_from_record(expression=expression, payload=payload))
                    seen_expressions.add(expression)
            elif path.suffix == ".csv":
                with path.open("r", encoding="utf-8", errors="ignore", newline="") as handle:
                    for row in csv.DictReader(handle):
                        if len(ideas) >= limit:
                            return ideas
                        expression = str(row.get("expression", "")).strip()
                        if not expression or expression in seen_expressions:
                            continue
                        ideas.append(self._idea_from_record(expression=expression, payload=row))
                        seen_expressions.add(expression)
        return ideas

    def _idea_from_record(self, *, expression: str, payload: dict) -> StrategyIdea:
        plan = parse_alpha_expression(expression)
        family = str(payload.get("family") or payload.get("meta", {}).get("family") or "worldquant_port")
        score = (
            payload.get("risk_adjusted_score")
            or payload.get("heuristic_score")
            or payload.get("metrics", {}).get("sharpe")
            or "unknown"
        )
        hypothesis = (
            f"Port the {family} methodology into BTC/USDT carry-compatible crypto factors. "
            f"Source score={score}; operators={','.join(operator.value for operator in plan.operators) or 'none'}."
        )
        return StrategyIdea(
            title=f"WQ Port {family} {len(plan.inputs) or 1} inputs",
            source="worldquant_local_alpha",
            market="crypto_perp",
            symbol_scope=["BTC/USDT"],
            hypothesis_summary=hypothesis,
            source_ref=expression[:255],
            rationale=(
                "Imported as methodology only from the local alpha workspace; "
                "original equity expression must be reinterpreted for crypto data."
            ),
            intake_bucket="rule_candidate",
        )
