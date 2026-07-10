"""Data ingestion application helpers."""

from __future__ import annotations

from shared.config import settings
from shared.models import IngestionJob

from .universe import FIXED_TOP20_SYMBOLS

DEFAULT_BINANCE_TOP20 = [
    *FIXED_TOP20_SYMBOLS,
]


def resolve_binance_live_ws_symbols() -> list[str]:
    """Resolve WS collector symbols; 'top20' expands to DEFAULT_BINANCE_TOP20."""
    raw = (settings.binance_live_ws_symbols or "").strip()
    if not raw or raw.lower() == "top20":
        return list(DEFAULT_BINANCE_TOP20)
    return [item.strip() for item in raw.split(",") if item.strip()]


class IngestionService:
    """Prepare source-ingestion jobs without doing network I/O in API handlers."""

    def prepare_job(self, job: IngestionJob) -> IngestionJob:
        if (
            job.source_family.upper() in {"A", "A_MARKET"}
            and job.source_name.lower() == "binance"
            and not job.target_symbols
        ):
            target_symbols = DEFAULT_BINANCE_TOP20
            if job.job_type == "binance_ohlcv_backfill":
                target_symbols = ["BTC/USDT", "BTC/USDT:USDT"]
            if job.job_type in {"binance_funding_backfill", "binance_live_market_collector"}:
                target_symbols = [f"{symbol}:USDT" for symbol in DEFAULT_BINANCE_TOP20]
            return job.model_copy(
                update={
                    "target_symbols": target_symbols,
                    "execution_summary": {
                        **job.execution_summary,
                        "universe_source": "first_tranche_binance_public_market_defaults",
                    },
                }
            )
        return job
