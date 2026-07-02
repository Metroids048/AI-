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
        ...

    def operators_catalog(self) -> list[AlphaOperator]:
        """Operators currently supported by the crypto port."""
        return list(AlphaOperator)
