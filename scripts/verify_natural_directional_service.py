"""Offline proof of the natural directional runtime and API projection.

This verifier deliberately does NOT call Testnet acceptance and does NOT close
positions. It uses the production strategy, scheduler, gatekeeper, normalizer,
exchange-first projection and FastAPI read endpoints. The external venue is a
strict deterministic emulator because this repository package contains no
operator Binance credentials. A real Binance Demo order still requires the
operator device and is never claimed by this script.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
import types
import urllib.request
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

# The verification database must be selected before service imports initialize settings/engines.
DEFAULT_ROOT = Path("artifacts/natural-directional-service-proof")


def _install_celery_contract_stub() -> None:
    """Supply only the callable checked by RuntimeScheduler.start()."""

    if "celery" in sys.modules:
        return
    module = types.ModuleType("celery")

    def shared_task(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        if args and callable(args[0]) and len(args) == 1 and not kwargs:
            fn = args[0]
            fn.run = fn
            return fn

        def decorate(fn):  # noqa: ANN001, ANN202
            fn.run = fn
            return fn

        return decorate

    module.shared_task = shared_task
    sys.modules["celery"] = module


def _seed_trend_bars(session, *, symbol: str, sign: int) -> None:  # noqa: ANN001
    from services.data import DataRepository

    specs = {
        "4h": (100.0 if sign > 0 else 300.0, 0.20 * sign, 0.0),
        # Intentionally disagrees with 4h so the real primary candidate starves
        # and the bounded entry+one-higher candidate proves natural fallback.
        "1h": (200.0, -0.10 * sign, 0.0),
        "15m": (100.0 if sign > 0 else 300.0, 0.03 * sign, 0.50 * sign),
    }
    duration = {"15m": timedelta(minutes=15), "1h": timedelta(hours=1), "4h": timedelta(hours=4)}
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    repo = DataRepository(session)
    for timeframe, (base, step, acceleration) in specs.items():
        price = base
        rows: list[dict[str, Any]] = []
        for index in range(240):
            increment = step + (acceleration if index >= 233 else 0.0)
            open_price = price
            close_price = price + increment
            price = close_price
            rows.append(
                {
                    "symbol": symbol,
                    "exchange": "binance",
                    "timeframe": timeframe,
                    "time": now - duration[timeframe] * (240 - index),
                    "open": Decimal(str(open_price)),
                    "high": Decimal(str(max(open_price, close_price) + 0.5)),
                    "low": Decimal(str(min(open_price, close_price) - 0.5)),
                    "close": Decimal(str(close_price)),
                    "volume": Decimal("100"),
                }
            )
        repo.store_ohlcv_bars(rows)


class VerificationExchangeEmulator:
    """Strict exchange boundary emulator; never represented as Binance evidence."""

    capability = type("Capability", (), {"gateway_name": "verification_exchange_emulator"})()

    def __init__(self) -> None:
        self.submitted: list[Any] = []

    def account_equity(self) -> float:
        return 10_000.0

    def sync_account(self, *, live_run_id: str):  # noqa: ANN201
        from shared.models import ExchangeAccountSnapshot

        del live_run_id
        return ExchangeAccountSnapshot(
            exchange="verification_exchange_emulator",
            wallet_balance=10_000.0,
            margin_balance=10_000.0,
            available_balance=10_000.0,
            snapshot_time=datetime.now(UTC),
        )

    def reconcile(self, *, live_run_id: str) -> dict[str, Any]:
        del live_run_id
        return {"open_positions": [], "open_orders": []}

    def load_market_rules_snapshot(self, *, symbol: str, leverage: float, loaded_at: datetime):  # noqa: ANN201
        from shared.models import MarketRulesSnapshot

        return MarketRulesSnapshot(
            rules_snapshot_id=f"verification-rules:{symbol}",
            symbol=symbol,
            market_status="TRADING",
            position_mode="ONE_WAY",
            margin_mode="CROSS",
            leverage=leverage,
            tick_size=Decimal("0.1"),
            step_size=Decimal("0.001"),
            min_quantity=Decimal("0.001"),
            min_notional=Decimal("5"),
            loaded_at=loaded_at,
            exchange="verification_exchange_emulator",
            market_type="swap",
            exchange_symbol=symbol.replace("/", "") + ":USDT",
            price_precision=1,
            amount_precision=3,
            contract_size=Decimal("1"),
            market_active=True,
        )

    def submit_order(self, *, live_run_id: str, order_request):  # noqa: ANN001, ANN201
        del live_run_id
        assert order_request.trade_intent is not None, "trade_intent_missing"
        assert order_request.market_rules_snapshot is not None, "market_rules_snapshot_missing"
        assert order_request.entry_context.get("decision_variant") == "simulation_sampling_fallback", (
            "wrong_decision_variant"
        )
        assert order_request.entry_context.get("strategy_performance_eligible") is False, (
            "fallback_pollutes_primary_performance"
        )
        assert order_request.entry_context.get("sampling_performance_eligible") is True, "sampling_attribution_missing"
        self.submitted.append(order_request)
        quantity = float(order_request.trade_intent.target_quantity)
        reference = float(order_request.entry_context["reference_price"])
        order_number = len(self.submitted)
        return {
            "gateway_order_id": f"VERIFICATION-NATURAL-{order_number}",
            "gateway_status": "filled",
            "quantity": quantity,
            "filled_quantity": quantity,
            "average_fill_price": reference + (0.1 if order_request.direction.value == "long" else -0.1),
            "fill_timestamp": datetime.now(UTC).isoformat(),
            "fill_source": "verification_exchange_emulator",
            "protection_order_refs": [
                {"algoId": f"VERIFY-STOP-{order_number}", "orderType": "STOP_MARKET"},
                {"algoId": f"VERIFY-TP-{order_number}", "orderType": "TAKE_PROFIT_MARKET"},
            ],
        }


def _build_runtime(db_url: str):  # noqa: ANN201
    from services.data import DataRepository
    from services.data.universe import AUTO_SIMULATION_EXECUTION_SYMBOLS, execution_scope_hash
    from services.database import create_local_runtime_schema, get_session_factory, reset_database_caches
    from services.execution.gatekeeper import ExecutionGatekeeperService
    from services.execution.paper_runtime import PaperRuntimeService
    from services.strategy_library import (
        AgentTaskRepository,
        ConfigSnapshotRepository,
        ExecutionRepository,
        HypothesisRepository,
        NotificationRepository,
        PaperRunRepository,
        ReviewRepository,
        RiskProfileRepository,
        StrategyRepository,
        ValidationRepository,
    )
    from services.strategy_library.candidates.registry import get_candidate
    from shared.models import BacktestRun, ConfigSnapshot, GateDecision, PaperRun, StrategyCreate
    from shared.models.risk import medium_risk_profile

    reset_database_caches()
    create_local_runtime_schema(db_url)
    session = get_session_factory(db_url)()
    candidate = get_candidate("trend_momentum_v1")
    strategy = StrategyRepository(session).create_strategy(
        StrategyCreate(
            strategy_key="auto_paper_mature_templates",
            source="natural-service-verification",
            core_thesis="Production directional candidate verified through natural signals",
            symbol_scope=list(AUTO_SIMULATION_EXECUTION_SYMBOLS),
            timeframe="15m",
            rules=candidate.get_config(),
        )
    )
    backtest = ValidationRepository(session).create_backtest_run(
        BacktestRun(
            strategy_id=strategy.strategy_id,
            execution_engine="natural-service-verification",
            eligibility_result=GateDecision(
                strategy_id=strategy.strategy_id,
                passed=True,
                decision_status="accepted",
                reason="verification fixture admission only",
            ),
        )
    )
    risk = RiskProfileRepository(session).create_profile(medium_risk_profile())
    profile = {
        "auto_paper_runtime_key": "auto_paper_mature_templates",
        "strategy_lane": "directional",
        "execution_mode": "binance_simulation_first",
        "mirror_to_gateway": True,
        "cost_gate_verified": True,
        "simulation_sampling_fallback_enabled": True,
        "acceptance_symbols": list(AUTO_SIMULATION_EXECUTION_SYMBOLS),
        "acceptance_scope_hash": execution_scope_hash(),
        "risk_profile_id": risk.risk_profile_id,
        "account_equity": 10_000.0,
        "equity_peak": 10_000.0,
        "auto_schedule_enabled": True,
        "llm_veto_enabled": False,
        "market_intelligence_enabled": False,
    }
    run = PaperRunRepository(session).create_paper_run(
        PaperRun(
            strategy_id=strategy.strategy_id,
            gate_decision_ref=backtest.backtest_run_id,
            symbol_scope=list(AUTO_SIMULATION_EXECUTION_SYMBOLS),
            candidate_symbols=list(AUTO_SIMULATION_EXECUTION_SYMBOLS),
            execution_profile=profile,
            paper_metrics_summary={"account_equity": 10_000.0, "equity_peak": 10_000.0},
            paper_status="running",
        )
    )
    ConfigSnapshotRepository(session).create_snapshot(
        ConfigSnapshot.create(
            paper_run_id=run.paper_run_id or "",
            config={
                "execution_profile": profile,
                "strategy_rules": strategy.rules.model_dump(mode="json"),
                "risk_profile_id": risk.risk_profile_id,
            },
            created_by="natural-service-verification",
            effective_cycle_id="seed",
        ),
        base_config_hash=None,
    )
    _seed_trend_bars(session, symbol="BTC/USDT", sign=1)
    _seed_trend_bars(session, symbol="ETH/USDT", sign=-1)
    gateway = VerificationExchangeEmulator()
    data_repo = DataRepository(session)
    execution_repo = ExecutionRepository(session)
    runtime = PaperRuntimeService(
        data_repo=data_repo,
        execution_repo=execution_repo,
        paper_repo=PaperRunRepository(session),
        strategy_repo=StrategyRepository(session),
        agent_repo=AgentTaskRepository(session),
        review_repo=ReviewRepository(session),
        notification_repo=NotificationRepository(session),
        gatekeeper=ExecutionGatekeeperService(
            data_repo=data_repo,
            validation_repo=ValidationRepository(session),
            hypothesis_repo=HypothesisRepository(session),
            risk_profile_repo=RiskProfileRepository(session),
            execution_repo=execution_repo,
            paper_repo=PaperRunRepository(session),
            review_repo=ReviewRepository(session),
        ),
        gateway=gateway,
    )
    return session, runtime, run, gateway


async def _run_scheduler(runtime, run, db_url: str):  # noqa: ANN001, ANN201
    import services.execution.scheduler as scheduler_module
    from services.database import get_session_factory
    from services.execution.scheduler import RuntimeScheduler
    from services.execution.scheduler_coordination import SchedulerCoordinator
    from shared.models import PaperRuntimeCycleRequest

    _install_celery_contract_stub()
    no_op = lambda: {"status": "verification_noop"}  # noqa: E731
    scheduler_module._default_exchange_info_refresh_runner = no_op
    cycle_results: list[dict[str, Any]] = []

    def paper_runner() -> dict[str, Any]:
        fencing_token = coordinator.fencing_token(lease_name="paper_runtime_cycle")
        result = runtime.run_cycle(
            paper_run_id=run.paper_run_id or "",
            request=PaperRuntimeCycleRequest(
                symbols=["BTC/USDT", "ETH/USDT"],
                timeframe="15m",
                enable_decision_veto=False,
                scheduled_for=datetime.now(UTC),
                scheduler_instance_id="natural-service-verifier",
                cycle_source="runtime_scheduler",
                run_mode="paper",
                deployment_sha="natural-service-verification",
                process_id=os.getpid(),
                container_id="verification-container",
                lease_name="paper_runtime_cycle",
                fencing_token=fencing_token,
            ),
        )
        payload = result.model_dump(mode="json")
        cycle_results.append(payload)
        return payload

    coordinator = SchedulerCoordinator(
        session_factory=get_session_factory(db_url), instance_id="natural-service-verifier"
    )
    scheduler = RuntimeScheduler(
        paper_cycle_seconds=0.4,
        heartbeat_seconds=3600,
        notification_seconds=3600,
        news_poll_seconds=3600,
        macro_poll_seconds=3600,
        social_poll_seconds=3600,
        risk_sweep_seconds=3600,
        edge_stats_refresh_seconds=3600,
        daily_review_check_seconds=3600,
        paper_cycle_runner=paper_runner,
        heartbeat_runner=no_op,
        news_poll_runner=no_op,
        macro_poll_runner=no_op,
        social_poll_runner=no_op,
        risk_sweep_runner=no_op,
        edge_stats_refresh_runner=no_op,
        notification_runner=no_op,
        daily_review_runner=lambda _date=None: {"status": "verification_noop"},
        coordinator=coordinator,
        scheduler_instance_id="natural-service-verifier",
    )
    scheduler.start()
    await asyncio.sleep(1.35)
    await scheduler.stop()
    return scheduler.status.model_dump(), cycle_results


def _request_json(url: str, *, token: str | None = None) -> Any:
    request = urllib.request.Request(url)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


def _serve_and_query(*, db_url: str, run_id: str, port: int, output_dir: Path) -> dict[str, Any]:
    env = os.environ.copy()
    env.update(
        {
            "POSTGRES_URL": db_url,
            "PAPER_CONSOLE_API_ONLY": "true",
            "RUNTIME_SCHEDULER_AUTOSTART": "false",
            "ADMIN_API_TOKEN": "natural-proof-token",
            "APP_ENV": "test",
        }
    )
    log_path = output_dir / "uvicorn.log"
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(  # noqa: S603
            [sys.executable, "-m", "uvicorn", "apps.api.main:app", "--host", "127.0.0.1", "--port", str(port)],
            cwd=Path(__file__).resolve().parents[1],
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.time() + 15
            while time.time() < deadline:
                try:
                    health = _request_json(f"http://127.0.0.1:{port}/health")
                    break
                except Exception:
                    time.sleep(0.2)
            else:
                raise RuntimeError("FastAPI service did not become healthy")
            token = "natural-proof-token"
            return {
                "health": health,
                "runtime_status": _request_json(
                    f"http://127.0.0.1:{port}/api/v1/execution/paper-runs/{run_id}/runtime-status", token=token
                ),
                "orders": _request_json(f"http://127.0.0.1:{port}/api/v1/execution/orders", token=token),
                "positions": _request_json(f"http://127.0.0.1:{port}/api/v1/execution/positions", token=token),
            }
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default=str(DEFAULT_ROOT))
    parser.add_argument("--port", type=int, default=18016)
    args = parser.parse_args()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    db_path = output_dir / "natural-runtime.sqlite"
    if db_path.exists():
        db_path.unlink()
    db_url = f"sqlite:///{db_path.as_posix()}"
    os.environ["POSTGRES_URL"] = db_url

    from shared.config import settings

    settings.binance_auto_execute = True
    settings.binance_use_testnet = True
    settings.live_trading_enabled = False
    settings.market_intelligence_enabled = False
    settings.binance_live_ws_enabled = False
    session, runtime, run, gateway = _build_runtime(db_url)
    scheduler_status, cycle_results = asyncio.run(_run_scheduler(runtime, run, db_url))

    from services.strategy_library import DecisionSnapshotRepository, ExecutionRepository

    execution_repo = ExecutionRepository(session)
    orders = execution_repo.list_orders()
    positions = execution_repo.list_latest_positions_for_run(run_type="paper", run_id=run.paper_run_id or "")
    decisions = DecisionSnapshotRepository(session).list_snapshots(paper_run_id=run.paper_run_id)
    assert len(gateway.submitted) == 2, f"expected two natural exchange submissions, got {len(gateway.submitted)}"
    assert {order.symbol for order in orders} == {"BTC/USDT", "ETH/USDT"}
    assert all(order.gateway_status == "filled" for order in orders)
    assert len(positions) == 2 and all(position.quantity > 0 for position in positions)
    assert all(order.entry_context.get("decision_variant") == "simulation_sampling_fallback" for order in orders)
    assert all(order.entry_context.get("strategy_performance_eligible") is False for order in orders)
    # No quick round trip: both latest position snapshots remain non-zero after repeated scheduler ticks.
    assert all(position.quantity > 0 for position in positions)

    api = _serve_and_query(db_url=db_url, run_id=run.paper_run_id or "", port=args.port, output_dir=output_dir)
    proof = {
        "proof_type": "natural_strategy_service_with_strict_exchange_emulator",
        "real_binance_order_claimed": False,
        "network_order_calls": 0,
        "acceptance_or_manual_orders": 0,
        "positions_left_open": True,
        "database": str(db_path),
        "paper_run_id": run.paper_run_id,
        "scheduler_status": scheduler_status,
        "cycle_results": cycle_results,
        "gateway_submissions": [
            {
                "symbol": request.symbol,
                "direction": request.direction.value,
                "candidate_id": request.entry_context.get("candidate_id"),
                "decision_variant": request.entry_context.get("decision_variant"),
                "primary_rejection_reason": request.entry_context.get("primary_rejection_reason"),
                "target_quantity": str(request.trade_intent.target_quantity if request.trade_intent else ""),
            }
            for request in gateway.submitted
        ],
        "orders": [order.model_dump(mode="json") for order in orders],
        "positions": [position.model_dump(mode="json") for position in positions],
        "decision_snapshots": [decision.model_dump(mode="json") for decision in decisions],
        "api": api,
        "limitation": (
            "No .env/Binance credentials were present in the uploaded repository; "
            "real Demo order IDs cannot be created here."
        ),
    }
    proof_path = output_dir / "proof.json"
    proof_path.write_text(json.dumps(proof, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": "PASS",
                "proof": str(proof_path),
                "natural_orders": len(orders),
                "open_positions": len(positions),
                "symbols": sorted(order.symbol for order in orders),
                "api_health": api["health"],
                "real_binance_order_claimed": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
