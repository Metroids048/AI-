"""Parse a WorldQuant-style alpha expression into a structured AlphaPlan.

Stub seam (P1-03). Consumes the operator vocabulary in operators.py and emits a
`shared.models.AlphaPlan` whose `target_market` defaults to crypto.
"""

from __future__ import annotations

import re

from shared.models import AlphaOperator, AlphaPlan

SUPPORTED_OPERATORS = {operator.value: operator for operator in AlphaOperator}
IDENTIFIER_RE = re.compile(r"\b[a-zA-Z_][a-zA-Z0-9_]*\b")
NUMBER_RE = re.compile(r"^-?\d+(\.\d+)?$")


def parse_alpha_expression(expr: str) -> AlphaPlan:
    """Parse `expr` (e.g. "rank(close/delay(close,5))") into an AlphaPlan."""
    tokens = IDENTIFIER_RE.findall(expr)
    operators: list[AlphaOperator] = []
    inputs: list[str] = []
    parameters: dict[str, int | float | str] = {}

    for token in tokens:
        lower = token.lower()
        if lower in SUPPORTED_OPERATORS:
            operators.append(SUPPORTED_OPERATORS[lower])
            continue
        if NUMBER_RE.match(token):
            continue
        if lower not in {"industry", "sector", "subindustry"}:
            inputs.append(token)

    window_matches = re.findall(r"(?:delay|ts_delta|ts_mean|ts_std|correlation|decay_linear)\([^)]*,\s*(\d+)\)", expr)
    if window_matches:
        parameters["windows"] = [int(match) for match in window_matches]
        parameters["max_window"] = max(parameters["windows"])

    unique_operators = list(dict.fromkeys(operators))
    unique_inputs = list(dict.fromkeys(inputs))
    return AlphaPlan(
        raw_expression=expr,
        operators=unique_operators,
        inputs=unique_inputs,
        parameters=parameters,
    )
