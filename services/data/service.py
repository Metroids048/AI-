"""Data ingestion application helpers."""

from __future__ import annotations

from shared.models import IngestionJob

DEFAULT_BINANCE_TOP20 = [
    "BTC/USDT",
    "ETH/USDT",
    "BNB/USDT",
    "SOL/USDT",
    "XRP/USDT",
    "DOGE/USDT",
    "ADA/USDT",
    "TRX/USDT",
    "AVAX/USDT",
    "LINK/USDT",
    "DOT/USDT",
    "MATIC/USDT",
    "LTC/USDT",
    "BCH/USDT",
    "UNI/USDT",
    "ATOM/USDT",
    "ETC/USDT",
    "FIL/USDT",
    "APT/USDT",
    "ARB/USDT",
]


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
                target_symbols = ["BTC/USDT:USDT"]
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
