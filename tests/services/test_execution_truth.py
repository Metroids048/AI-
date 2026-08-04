from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from services.execution.execution_truth import validate_pretrade_snapshot
from shared.config import settings
from shared.models import PretradeMarketSnapshot


def _snapshot(*, mark_price: str, atr: str = "0.1") -> PretradeMarketSnapshot:
    now = datetime.now(UTC)
    return PretradeMarketSnapshot(
        server_time=now,
        bid=Decimal(mark_price) - Decimal("0.01"),
        ask=Decimal(mark_price) + Decimal("0.01"),
        mark_price=Decimal(mark_price),
        decision_bar_close_time=now - timedelta(seconds=10),
        decision_age_seconds=10,
        atr=Decimal(atr),
        tick_size=Decimal("0.01"),
        step_size=Decimal("0.001"),
    )


def test_legacy_pretrade_defaults_match_frozen_strict_limits() -> None:
    assert settings.pretrade_min_price_drift_bps == 20.0
    assert settings.pretrade_atr_drift_fraction == 0.25


def test_legacy_pretrade_rejects_fifty_bps_drift() -> None:
    with pytest.raises(ValueError, match="PRETRADE_PRICE_DRIFT"):
        validate_pretrade_snapshot(
            _snapshot(mark_price="100.50"),
            decision_reference=Decimal("100"),
        )


def test_legacy_pretrade_accepts_exact_twenty_bps_boundary() -> None:
    assert validate_pretrade_snapshot(
        _snapshot(mark_price="100.20"),
        decision_reference=Decimal("100"),
    ) == Decimal("0.002")
