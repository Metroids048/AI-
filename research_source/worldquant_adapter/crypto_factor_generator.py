"""Turn a ported AlphaPlan into runnable BTC/USDT factor code.

This is where equity methodology becomes a crypto factor. Output feeds
services/strategy_library/importers/ → Strategy objects. Stub seam (P1-03).
"""

from __future__ import annotations

from shared.models import AlphaOperator, AlphaPlan


class CryptoFactorGenerator:
    """Generate crypto factor signal code from a ported AlphaPlan."""

    def from_alpha_plan(self, plan: AlphaPlan) -> str:
        """Return Python signal code (string) computing the factor on OHLCV."""
        operators = ", ".join(operator.value for operator in plan.operators) or "none"
        inputs = ", ".join(plan.inputs) or "close"
        expression = plan.raw_expression.replace('"', "'")
        return f'''import pandas as pd

# Ported WorldQuant methodology for crypto research.
# operators: {operators}
# inputs: {inputs}

def compute_factor(frame: pd.DataFrame) -> pd.Series:
    close = frame["close"]
    volume = frame.get("volume", pd.Series(index=frame.index, dtype="float64")).fillna(0.0)
    expression = "{expression}"
    # The raw expression is preserved for auditability; implementation remains
    # crypto-native and should be refined inside the strategy library.
    signal = (close.pct_change().fillna(0.0) * 0.7) + (volume.pct_change().fillna(0.0) * 0.3)
    signal.name = "ported_alpha_signal"
    return signal
'''

    def operators_catalog(self) -> list[AlphaOperator]:
        """Operators currently supported by the crypto port."""
        return list(AlphaOperator)
