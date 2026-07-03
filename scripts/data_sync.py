"""Explicit data-sync entrypoint placeholder for API-backed ingestion jobs."""

from __future__ import annotations


def main() -> int:
    print(
        "Data sync is API/task driven. Submit /api/v1/ingestion/jobs with "
        "job_type=binance_ohlcv_backfill or binance_funding_backfill."
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
