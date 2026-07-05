"""Scan the local alpha workspace and turn candidates into StrategyIdea seeds."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pandas as pd

from shared.models import Market, StrategyIdea

from .expression_evaluator import UnsupportedAlphaExpression, evaluate_alpha_plan
from .expression_parser import parse_alpha_expression

SUPPORTED_FILES = (
    "alpha_candidates.jsonl",
    "hopeful_alphas.jsonl",
    "alpha_generated_expressions.csv",
    "通过门槛的alpha.csv",
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
        evaluation_error: str | None = None
        if plan.evaluable:
            try:
                evaluate_alpha_plan(plan, self._sample_frame())
            except UnsupportedAlphaExpression as exc:
                plan = plan.model_copy(
                    update={
                        "evaluable": False,
                        "unsupported_operators": [*plan.unsupported_operators, "<runtime_reject>"],
                    }
                )
                evaluation_error = str(exc)

        family = str(
            payload.get("family")
            or payload.get("profile")
            or payload.get("meta", {}).get("family")
            or "worldquant_port"
        )
        score = (
            payload.get("risk_adjusted_score")
            or payload.get("heuristic_score")
            or payload.get("sharpe")
            or payload.get("metrics", {}).get("sharpe")
            or "unknown"
        )
        metadata = self._intake_metadata(plan=plan, payload=payload, evaluation_error=evaluation_error)
        intake_bucket = "rule_candidate" if plan.evaluable else "subjective_to_drop"
        hypothesis = (
            f"Port the {family} methodology into BTC/USDT crypto factors. "
            f"score={score}; behavior={plan.behavior_signature or 'unknown'}; "
            f"operators={','.join(operator.value for operator in plan.operators) or 'none'}."
        )
        return StrategyIdea(
            title=f"WQ Port {family} {len(plan.inputs) or 1} inputs",
            source="worldquant_local_alpha",
            market=Market.CRYPTO_PERP,
            symbol_scope=["BTC/USDT"],
            hypothesis_summary=hypothesis,
            source_ref=expression[:255],
            rationale=(
                "Imported as methodology only from the local alpha workspace; "
                "crypto execution evidence="
                f"{json.dumps(metadata, ensure_ascii=False, sort_keys=True)}"
            ),
            intake_metadata=metadata,
            intake_bucket=intake_bucket,
        )

    @staticmethod
    def _intake_metadata(*, plan, payload: dict, evaluation_error: str | None) -> dict:
        return {
            "alpha_id": payload.get("alpha_id"),
            "raw_expression": plan.raw_expression,
            "operators": [operator.value for operator in plan.operators],
            "windows": plan.windows,
            "group_aliases": plan.group_aliases,
            "behavior_signature": plan.behavior_signature,
            "supported_inputs": plan.supported_inputs,
            "unsupported_inputs": plan.unsupported_inputs,
            "unsupported_operators": plan.unsupported_operators,
            "evaluable": plan.evaluable,
            "evaluation_error": evaluation_error,
            "source_metrics": {
                "sharpe": payload.get("sharpe") or payload.get("metrics", {}).get("sharpe"),
                "fitness": payload.get("fitness"),
                "turnover": payload.get("turnover"),
                "returns": payload.get("returns"),
                "drawdown": payload.get("drawdown"),
            },
            "source_flags": {
                "profile": payload.get("profile"),
                "metric_gate_pass": payload.get("metric_gate_pass"),
                "platform_non_self_pass": payload.get("platform_non_self_pass"),
                "submission_candidate": payload.get("submission_candidate"),
                "blocked_reason": payload.get("blocked_reason"),
                "failure_reasons": payload.get("failure_reasons"),
            },
        }

    @staticmethod
    def _sample_frame() -> pd.DataFrame:
        index = pd.RangeIndex(start=0, stop=64, step=1)
        base = pd.Series(range(64), index=index, dtype="float64")
        return pd.DataFrame(
            {
                "open": 100 + base,
                "high": 101 + base,
                "low": 99 + base,
                "close": 100.5 + base,
                "volume": 1_000 + (base * 10),
                "vwap": 100.25 + base,
                "funding_rate": ((base % 5) - 2) * 0.0001,
                "open_interest": 50_000 + (base * 100),
                "long_ratio": 0.45 + ((base % 4) * 0.05),
                "short_ratio": 0.55 - ((base % 4) * 0.05),
                "liquidation_usd": 100 + (base * 3),
            },
            index=index,
        )
