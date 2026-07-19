"""Generate the 90-day Top20 technical-template comparison audit.

This script is intentionally evidence-only: it stores public historical OHLCV
for replay and writes an audit report, but never enables an automatic strategy
or changes Paper/Testnet execution settings.
"""

from __future__ import annotations

import argparse
import os
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from services.data import DataRepository
from services.data.binance import BinanceCcxtClient
from services.data.universe import AUTO_PAPER_RESEARCH_SYMBOLS
from services.database import get_session_factory
from services.execution.bootstrap import (
    AUTO_PAPER_TECHNICAL_KEY,
    AUTO_PAPER_TECHNICAL_RULES,
)
from services.validation.technical_replay import MarketData, TechnicalStrategyValidationService
from shared.models import OHLCVBar, StrategyContract, StrategyRules, Timeframe

ONE_HOUR_BASELINE_STRATEGY_KEY = "validated_template_1h"


def _closed_four_hour_boundary(now: datetime) -> datetime:
    rounded = now.astimezone(UTC).replace(hour=(now.hour // 4) * 4, minute=0, second=0, microsecond=0)
    return rounded - timedelta(hours=4)


def _template(
    *, strategy_key: str, rules: dict, timeframe: Timeframe, symbols: tuple[str, ...] = AUTO_PAPER_RESEARCH_SYMBOLS
) -> StrategyContract:
    return StrategyContract(
        strategy_id=strategy_key,
        strategy_key=strategy_key,
        source="platform:technical_validation",
        core_thesis="Offline comparison only; this object cannot arm Paper or Testnet execution.",
        symbol_scope=list(symbols),
        timeframe=timeframe,
        rules=StrategyRules(**rules),
    )


def _comparison_templates() -> tuple[StrategyContract, StrategyContract]:
    shared_rules = deepcopy(AUTO_PAPER_TECHNICAL_RULES)
    shared_rules["exit_rules"] = {}
    shared_rules["takeprofit_rules"] = {
        "risk_reward": float(AUTO_PAPER_TECHNICAL_RULES["takeprofit_rules"]["risk_reward"])
    }
    baseline_rules = deepcopy(shared_rules)
    baseline_entry_rules = baseline_rules["entry_rules"]
    baseline_entry_rules["entry_timeframe"] = "1h"
    for key in ("direction_timeframe", "state_timeframe", "timeframe_model"):
        baseline_entry_rules.pop(key, None)
    baseline = _template(
        strategy_key=ONE_HOUR_BASELINE_STRATEGY_KEY,
        rules=baseline_rules,
        timeframe=Timeframe.H1,
    )
    candidate = _template(
        strategy_key=AUTO_PAPER_TECHNICAL_KEY,
        rules=shared_rules,
        timeframe=Timeframe.M15,
    )
    return baseline, candidate


def _load_or_backfill(*, days: int, end_at: datetime) -> MarketData:
    start_at = end_at - timedelta(days=days)
    client = BinanceCcxtClient()
    market_data: MarketData = {}
    with get_session_factory()() as session:
        repository = DataRepository(session)
        for symbol in AUTO_PAPER_RESEARCH_SYMBOLS:
            market_data[symbol] = {}
            for timeframe in ("1h", "15m", "4h"):
                bars = client.fetch_ohlcv_history(
                    symbol=f"{symbol}:USDT",
                    timeframe=timeframe,
                    start_at=start_at,
                    end_at=end_at,
                )
                platform_bars: list[OHLCVBar | dict[str, Any]] = [bar.model_copy(update={"symbol": symbol}) for bar in bars]
                repository.store_ohlcv_bars(platform_bars)
                session.commit()
                market_data[symbol][timeframe] = platform_bars
    return market_data


def _load_stored(
    *, days: int, end_at: datetime, symbols: tuple[str, ...] = AUTO_PAPER_RESEARCH_SYMBOLS
) -> MarketData:
    start_at = end_at - timedelta(days=days)
    market_data: MarketData = {}
    with get_session_factory()() as session:
        repository = DataRepository(session)
        for symbol in symbols:
            market_data[symbol] = {
                timeframe: cast(
                    list,
                    repository.list_ohlcv_bars(
                        symbol=symbol,
                        timeframe=timeframe,
                        start_at=start_at,
                        end_at=end_at,
                    ),
                )
                for timeframe in ("1h", "15m", "4h")
            }
    return market_data


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare a 1h baseline with the current 4h/1h/15m policy.")
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument(
        "--database-url",
        required=True,
        help="Target database for stored/backfilled OHLCV history; required to avoid a separate validation store.",
    )
    parser.add_argument("--reuse-stored-data", action="store_true")
    args = parser.parse_args()
    if args.days < 60:
        raise SystemExit("--days must be at least 60 to retain 4h warmup and an OOS window")

    root = Path(__file__).resolve().parents[1]
    database_url = args.database_url
    os.environ["POSTGRES_URL"] = database_url
    from scripts.prepare_database import prepare_database

    prepare_database(database_url)
    end_at = _closed_four_hour_boundary(datetime.now(UTC))
    market_data = (
        _load_stored(days=args.days, end_at=end_at)
        if args.reuse_stored_data
        else _load_or_backfill(days=args.days, end_at=end_at)
    )
    baseline, candidate = _comparison_templates()
    report = TechnicalStrategyValidationService(max_workers=8).compare(
        baseline=baseline,
        candidate=candidate,
        market_data=market_data,
    )
    default_output = (
        root / "docs" / "audits" / f"{datetime.now(UTC).date().isoformat()}-top20-technical-validation.md"
    )
    output = args.output or default_output
    TechnicalStrategyValidationService.write_audit(report, output)
    print(output)
    print(f"promotion_allowed={report.promotion.allowed}")
    print(f"promotion_failed_reasons={','.join(report.promotion.failed_reasons) or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
