"""Parse a WorldQuant-style alpha expression into a structured AlphaPlan.

Stub seam (P1-03). Consumes the operator vocabulary in operators.py and emits a
`shared.models.AlphaPlan` whose `target_market` defaults to crypto.
"""

from __future__ import annotations

from shared.models import AlphaPlan


def parse_alpha_expression(expr: str) -> AlphaPlan:
    """Parse `expr` (e.g. "rank(close/delay(close,5))") into an AlphaPlan."""
    ...
