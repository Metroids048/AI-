from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest

from services.data.binance import BinanceCcxtClient


@pytest.mark.integration
@pytest.mark.skipif(os.getenv("RUN_BINANCE_INTEGRATION") != "1", reason="live Binance smoke is opt-in")
def test_binance_public_ohlcv_smoke() -> None:
    client = BinanceCcxtClient()
    try:
        end = datetime.now(UTC)
        bars = client.fetch_ohlcv_history(
            symbol="BTC/USDT",
            timeframe="1h",
            start_at=end - timedelta(hours=2),
            end_at=end,
            limit=3,
        )
    finally:
        client.close()
    assert bars
    assert bars[-1].symbol == "BTC/USDT"
