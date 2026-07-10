"""Run paper auto-cycles locally and print order/action evidence."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

db_path = ROOT / ".local_paper_console.db"
os.environ["POSTGRES_URL"] = f"sqlite:///{db_path.as_posix()}"
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("PAPER_RUNTIME_RELAXED_SIGNALS", "true")

from dotenv import dotenv_values  # noqa: E402

for key, value in dotenv_values(ROOT / ".env").items():
    if value is not None and key not in os.environ:
        os.environ[key] = value

from services.data import DataRepository  # noqa: E402
from services.database import get_session_factory, reset_database_caches  # noqa: E402
from services.execution.gatekeeper import ExecutionGatekeeperService  # noqa: E402
from services.execution.gateway import configured_gateways  # noqa: E402
from services.execution.paper_runtime import PaperRuntimeService  # noqa: E402
from services.strategy_library import (  # noqa: E402
    AgentTaskRepository,
    ExecutionRepository,
    HypothesisRepository,
    NotificationRepository,
    PaperRunRepository,
    ReviewRepository,
    RiskProfileRepository,
    StrategyRepository,
    ValidationRepository,
)
from shared.models import PaperRuntimeCycleRequest  # noqa: E402

reset_database_caches()


def build_runtime(session):  # noqa: ANN001
    return PaperRuntimeService(
        data_repo=DataRepository(session),
        execution_repo=ExecutionRepository(session),
        paper_repo=PaperRunRepository(session),
        strategy_repo=StrategyRepository(session),
        agent_repo=AgentTaskRepository(session),
        review_repo=ReviewRepository(session),
        notification_repo=NotificationRepository(session),
        gatekeeper=ExecutionGatekeeperService(
            data_repo=DataRepository(session),
            validation_repo=ValidationRepository(session),
            hypothesis_repo=HypothesisRepository(session),
            risk_profile_repo=RiskProfileRepository(session),
            execution_repo=ExecutionRepository(session),
            paper_repo=PaperRunRepository(session),
            review_repo=ReviewRepository(session),
        ),
        gateway=configured_gateways()[0],
    )


def main() -> int:
    session = get_session_factory()()
    try:
        paper_repo = PaperRunRepository(session)
        execution_repo = ExecutionRepository(session)
        runs = [r for r in paper_repo.list_paper_runs() if r.paper_status == "running"]
        print(f"running_paper_runs={len(runs)}")
        for run in runs:
            lane = run.execution_profile.get("strategy_lane", "?")
            print(f"  - {run.paper_run_id} lane={lane} symbols={len(run.candidate_symbols or [])}")

        if not runs:
            print("ERROR: no running paper runs — bootstrap did not create them")
            return 1

        runtime = build_runtime(session)
        request = PaperRuntimeCycleRequest(
            timeframe="1m",
            max_symbols=5,
            enable_decision_veto=True,
        )
        for cycle_idx in range(3):
            print(f"\n=== cycle {cycle_idx + 1} ===")
            for run in runs:
                result = runtime.run_cycle(paper_run_id=run.paper_run_id or "", request=request)
                print(
                    f"run {run.paper_run_id[:8]} lane={run.execution_profile.get('strategy_lane')}: "
                    f"opened={result.opened_positions} closed={result.closed_positions} "
                    f"rejected={result.rejected_orders} skipped={result.skipped_symbols}"
                )
                for action in result.actions[:8]:
                    trace = action.decision_trace or {}
                    print(
                        f"  {action.symbol} {action.action} "
                        f"status={trace.get('pipeline_status','')} "
                        f"reason={action.reason or trace.get('decision_reason','')}"
                    )

        orders = execution_repo.list_orders()
        filled = [o for o in orders if o.execution_status == "filled"]
        rejected = [o for o in orders if o.execution_status == "rejected"]
        print(f"\norders filled={len(filled)} rejected={len(rejected)} total={len(orders)}")
        for order in sorted(filled, key=lambda o: o.created_at or "", reverse=True)[:8]:
            ctx = order.entry_context or {}
            print(
                f"  FILLED {order.symbol} {order.direction} run={str(order.paper_run_id)[:8]} "
                f"pipeline={ctx.get('decision_pipeline', {}).get('pipeline_status', '')} at={order.created_at}"
            )
        for order in sorted(rejected, key=lambda o: o.created_at or "", reverse=True)[:5]:
            ctx = order.entry_context or {}
            tr = ctx.get("decision_pipeline", {})
            print(
                f"  REJECTED {order.symbol} pipeline={tr.get('pipeline_status')} "
                f"reasons={tr.get('rejection_reasons')} gate={order.rejection_reason}"
            )

        # Force one fresh cycle: clear idempotency keys so a new decision can run on latest bar.
        print("\n=== forced fresh cycle (cleared processed_cycle_keys) ===")
        for run in runs:
            metrics = dict(run.paper_metrics_summary)
            metrics["processed_cycle_keys"] = []
            paper_repo.update_paper_run(run.paper_run_id or "", paper_metrics_summary=metrics)
        session.commit()

        # Refresh market bars for Top20 via public REST before retry.
        from services.data.binance import BinanceCcxtClient  # noqa: E402

        client = BinanceCcxtClient()
        data_repo = DataRepository(session)
        symbols = ["BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT", "XRP/USDT"]
        for symbol in symbols:
            for tf in ("1m", "15m", "4h"):
                try:
                    bars = client.fetch_recent_ohlcv(symbol=symbol, timeframe=tf, limit=120)
                    written = data_repo.store_ohlcv_bars(bars)
                    print(f"  ohlcv {symbol} {tf}: fetched={len(bars)} written={written}")
                except Exception as exc:  # ponytail: best-effort refresh for smoke cycle
                    print(f"  warn: ohlcv refresh failed for {symbol} {tf}: {exc}")
        session.commit()

        from services.execution.bootstrap import bootstrap_clear_stale_blocking_risk_events  # noqa: E402

        cleared = bootstrap_clear_stale_blocking_risk_events()
        print(f"  cleared blocking risk events: {cleared}")
        fresh = data_repo.check_freshness(
            symbol="BTC/USDT",
            timeframe="1m",
            reference_time=__import__("datetime").datetime.now(__import__("datetime").UTC),
            max_delay=__import__("datetime").timedelta(seconds=7200),
        )
        print(f"  BTC 1m freshness: {fresh}")

        for run in runs:
            if run.execution_profile.get("strategy_lane") != "directional":
                continue
            result = runtime.run_cycle(
                paper_run_id=run.paper_run_id or "",
                request=PaperRuntimeCycleRequest(
                    timeframe="1m",
                    symbols=["BTC/USDT"],
                    max_symbols=1,
                    enable_decision_veto=False,
                ),
            )
            print(
                f"forced directional BTC: opened={result.opened_positions} closed={result.closed_positions} "
                f"rejected={result.rejected_orders} skipped={result.skipped_symbols}"
            )
            for action in result.actions:
                trace = action.decision_trace or {}
                print(f"  {action.symbol} {action.action} status={trace.get('pipeline_status')} reason={action.reason}")

        print("\n=== manual open + close (directional paper run) ===")
        from services.execution.manual import ManualTradingService  # noqa: E402
        from shared.models import ClosePositionRequest, ManualOrderRequest, TradeSide  # noqa: E402

        directional = next(
            (r for r in runs if r.execution_profile.get("strategy_lane") == "directional"),
            runs[0],
        )
        latest_btc = data_repo.get_latest_ohlcv_bar(symbol="BTC/USDT", timeframe="1m")
        if latest_btc is None:
            print("ERROR: still no BTC 1m bar after refresh")
            return 1
        ref_price = float(latest_btc.close)
        backtest_id = directional.gate_decision_ref or ""
        risk_profile_id = directional.execution_profile.get("risk_profile_id")
        manual = ManualTradingService(
            execution_repo=execution_repo,
            gatekeeper=build_runtime(session).gatekeeper,
            gateway=configured_gateways()[0],
        )
        open_req = ManualOrderRequest(
            mode="paper",
            strategy_id=directional.strategy_id,
            validation_backtest_run_id=backtest_id,
            risk_profile_id=risk_profile_id,
            paper_run_id=directional.paper_run_id,
            symbol="BTC/USDT",
            direction=TradeSide.LONG,
            quantity=0.001,
            reference_price=ref_price,
            leverage=2.0,
            timeframe="1m",
            stoploss_price=ref_price * 0.97,
            takeprofit_price=ref_price * 1.03,
            idempotency_key=f"verify-open-{int(ref_price)}",
        )
        open_order = manual.submit_manual_order(open_req)
        print(
            f"manual OPEN: status={open_order.execution_status} dir={open_order.direction} "
            f"reject={open_order.rejection_reason}"
        )
        close_req = ClosePositionRequest(
            mode="paper",
            strategy_id=directional.strategy_id,
            validation_backtest_run_id=backtest_id,
            risk_profile_id=risk_profile_id,
            paper_run_id=directional.paper_run_id,
            symbol="BTC/USDT",
            reference_price=ref_price,
            timeframe="1m",
            idempotency_key=f"verify-close-{int(ref_price)}",
        )
        try:
            close_order = manual.close_position(close_req)
            print(
                f"manual CLOSE: status={close_order.execution_status} reject={close_order.rejection_reason}"
            )
        except ValueError as exc:
            print(f"manual CLOSE skipped: {exc}")

        orders = execution_repo.list_orders()
        recent = sorted(orders, key=lambda o: o.created_at or "", reverse=True)[:10]
        print(f"\nrecent_orders_total={len(orders)} showing={len(recent)}")
        for order in recent:
            ctx = order.entry_context or {}
            print(
                f"  {order.symbol} {order.direction} {order.execution_status} "
                f"paper_run={order.paper_run_id} "
                f"pipeline={ctx.get('decision_pipeline', {}).get('pipeline_status', '')}"
            )
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
