"""Turn a parsed AlphaPlan into runnable crypto-native factor code."""

from __future__ import annotations

from shared.models import AlphaOperator, AlphaPlan


class CryptoFactorGenerator:
    """Generate crypto factor signal code from a ported AlphaPlan."""

    def from_alpha_plan(self, plan: AlphaPlan) -> str:
        """Return Python signal code (string) computing the factor on OHLCV."""
        if not plan.evaluable:
            unsupported = ",".join(plan.unsupported_operators + plan.unsupported_inputs) or "unknown"
            raise ValueError(f"alpha plan is not executable on crypto-native inputs: {unsupported}")
        operators = ", ".join(operator.value for operator in plan.operators) or "none"
        inputs = ", ".join(plan.inputs) or "close"
        windows = ", ".join(str(window) for window in plan.windows) or "none"
        group_aliases = ", ".join(f"{raw}->{mapped}" for raw, mapped in plan.group_aliases.items()) or "none"
        expression = repr(plan.raw_expression)
        return f'''import pandas as pd

# Ported WorldQuant methodology for crypto research.
# operators: {operators}
# inputs: {inputs}
# windows: {windows}
# groups: {group_aliases}

from research_source.worldquant_adapter.expression_evaluator import evaluate_alpha_expression

def compute_factor(frame: pd.DataFrame) -> pd.Series:
    expression = {expression}
    signal = evaluate_alpha_expression(expression, frame)
    signal.name = "ported_alpha_signal"
    return signal
'''

    def operators_catalog(self) -> list[AlphaOperator]:
        """Operators currently supported by the crypto port."""
        return list(AlphaOperator)
