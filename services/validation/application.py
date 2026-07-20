"""Application service for persisted carry backtests."""

from __future__ import annotations

import contextlib
from collections.abc import Iterable
from datetime import datetime, timedelta

from services.data.repository import DataRepository
from services.strategy_library import StrategyRepository, ValidationRepository
from shared.models import BacktestRun, CarryBacktestRequest

from .carry import CarryBacktestService


def _required_timestamp_gap_check(
    *,
    timestamps: Iterable[datetime],
    start_at: datetime,
    end_at: datetime,
    step: timedelta,
) -> dict:
    observed = set(timestamps)
    missing_timestamps: list[datetime] = []
    cursor = start_at
    while cursor <= end_at:
        if cursor not in observed:
            missing_timestamps.append(cursor)
        cursor += step
    return {
        "has_gaps": bool(missing_timestamps),
        "missing_timestamps": missing_timestamps,
        "expected_interval_seconds": step.total_seconds(),
    }


class CarryBacktestApplicationService:
    def __init__(
        self,
        *,
        strategy_repo: StrategyRepository,
        validation_repo: ValidationRepository,
        data_repo: DataRepository,
        carry_service: CarryBacktestService | None = None,
    ) -> None:
        self.strategy_repo = strategy_repo
        self.validation_repo = validation_repo
        self.data_repo = data_repo
        self.carry_service = carry_service or CarryBacktestService()

    def submit(self, request: CarryBacktestRequest) -> BacktestRun:
        strategy = self.strategy_repo.get_strategy(request.strategy_id)
        if strategy is None:
            raise ValueError(f"strategy not found: {request.strategy_id}")

        spot_bars = self.data_repo.list_ohlcv_bars(
            symbol=request.spot_symbol,
            timeframe=request.timeframe,
            start_at=request.start_at,
            end_at=request.end_at,
        )
        perp_bars = self.data_repo.list_ohlcv_bars(
            symbol=request.perp_symbol,
            timeframe=request.timeframe,
            start_at=request.start_at,
            end_at=request.end_at,
        )
        funding_rows = self.data_repo.list_market_extras(
            symbol=request.perp_symbol,
            start_at=request.start_at,
            end_at=request.end_at,
        )
        if not spot_bars or not perp_bars or not funding_rows:
            raise ValueError("carry backtest requires persisted spot, perp, and funding data")

        hold_hours = int(strategy.rules.exit_rules.get("hold_hours", 8))
        settlement_step = timedelta(hours=hold_hours)
        gap_check = {
            "spot": _required_timestamp_gap_check(
                timestamps=[bar.timestamp for bar in spot_bars],
                start_at=request.start_at,
                end_at=request.end_at,
                step=settlement_step,
            ),
            "perp": _required_timestamp_gap_check(
                timestamps=[bar.timestamp for bar in perp_bars],
                start_at=request.start_at,
                end_at=request.end_at,
                step=settlement_step,
            ),
        }
        combined_gap_check = {
            "has_gaps": gap_check["spot"]["has_gaps"] or gap_check["perp"]["has_gaps"],
            "missing_timestamps": sorted(
                {
                    *gap_check["spot"]["missing_timestamps"],
                    *gap_check["perp"]["missing_timestamps"],
                }
            ),
            "expected_interval_seconds": settlement_step.total_seconds(),
        }
        freshness_check = {
            "spot": self.data_repo.check_freshness(
                symbol=request.spot_symbol,
                timeframe=request.timeframe,
                reference_time=request.end_at,
                max_delay=settlement_step,
            ),
            "perp": self.data_repo.check_freshness(
                symbol=request.perp_symbol,
                timeframe=request.timeframe,
                reference_time=request.end_at,
                max_delay=settlement_step,
            ),
        }

        run = self.carry_service.run_backtest(
            strategy=strategy,
            spot_bars=spot_bars,
            perp_bars=perp_bars,
            funding_points=[
                {
                    "time": row.timestamp,
                    "funding_rate": row.funding_rate,
                }
                for row in funding_rows
                if row.funding_rate is not None
            ],
        )
        enriched_run = run.model_copy(
            update={
                "version_id": request.version_id,
                "dataset_scope": (
                    f"{request.spot_symbol}|{request.perp_symbol}|"
                    f"{request.timeframe}|{request.start_at.isoformat()}|{request.end_at.isoformat()}"
                ),
                "validation_methodology": {
                    **run.validation_methodology,
                    "data_quality": {
                        "gap_check": combined_gap_check,
                        "freshness_check": freshness_check,
                    },
                },
            }
        )
        persisted = self.validation_repo.create_backtest_run(enriched_run)
        # Write back the strategy lifecycle status — closes the state-machine
        # gap where backtest_status was defined on the Strategy table but never
        # updated, leaving it stuck at "not_started".
        if persisted.eligibility_result is not None:
            elig = persisted.eligibility_result
            # eligibility_result may be a dict (JSON column) or a GateDecision
            # pydantic model depending on the persistence path.
            if isinstance(elig, dict):  # noqa: SIM108 — mypy needs if/else to narrow GateDecision vs dict
                passed = elig.get("passed")
            else:
                passed = getattr(elig, "passed", None)
            backtest_status = "passed" if passed else "failed"
        else:
            backtest_status = "completed" if persisted.run_status == "completed" else "failed"
        with contextlib.suppress(Exception):
            self.strategy_repo.update_lifecycle_status(
                request.strategy_id,
                backtest_status=backtest_status,
            )
        return persisted
