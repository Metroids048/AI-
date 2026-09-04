from __future__ import annotations

import asyncio
import os
import tempfile
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from services.automated_trading.application.cycle_service import run_automated_trading_cycle
from services.automated_trading.infrastructure.account_writer import acquire_account_writer, bind_account
from services.automated_trading.infrastructure.runtime_lock import EngineActivation, resolve_engine_activation
from services.data import DataRepository
from services.execution.decision_pipeline import DecisionPipelineResult
from services.execution.gateway import BinanceUsdtPerpetualGateway
from services.execution.scheduler import RuntimeScheduler
from services.execution.tasks import run_all_paper_runtime_cycles
from services.execution.v2_scheduler_entry import resolve_scheduler_v2_jobs
from services.strategy_library import (
    ConfigSnapshotRepository,
    PaperRunRepository,
    RiskProfileRepository,
    StrategyRepository,
)
from shared.config import settings
from shared.models import (
    ConfigSnapshot,
    ExchangeAccountSnapshot,
    ExecutionOrderRequest,
    MarketRulesSnapshot,
    PaperRuntimeCycleRequest,
    PretradeMarketSnapshot,
    StrategyUpdate,
    TradeSide,
)
from shared.models.risk import medium_risk_profile
from tests.services.test_automated_trading_cycle import build_adapter_with_successful_cycle, build_request
from tests.services.test_paper_runtime import _runtime_without_position, _store_bar


class RecordingFilledGateway:
    capability = type("Cap", (), {"gateway_name": "binance_usdt_perpetual"})()

    def __init__(self) -> None:
        self.submitted: list[ExecutionOrderRequest] = []

    def account_equity(self) -> float:
        return 10_000.0

    def sync_account(self, *, live_run_id: str) -> ExchangeAccountSnapshot:
        del live_run_id
        return ExchangeAccountSnapshot(
            exchange="binance",
            wallet_balance=10_000.0,
            margin_balance=10_000.0,
            available_balance=10_000.0,
            snapshot_time=datetime.now(UTC),
        )

    def reconcile(self, *, live_run_id: str) -> dict:
        del live_run_id
        return {"open_positions": [], "open_orders": []}

    def load_market_rules_snapshot(self, *, symbol, leverage, loaded_at):  # noqa: ANN001
        return MarketRulesSnapshot(
            rules_snapshot_id=f"rules:{symbol}",
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
            exchange="binance",
            market_type="swap",
            exchange_symbol=f"{symbol}:USDT",
            price_precision=1,
            amount_precision=3,
            contract_size=Decimal("1"),
            market_active=True,
        )

    def pretrade_market_snapshot(self, *, order_request: ExecutionOrderRequest) -> PretradeMarketSnapshot:
        del order_request
        now = datetime.now(UTC)
        return PretradeMarketSnapshot(
            server_time=now,
            bid=Decimal("100.0"),
            ask=Decimal("100.1"),
            mark_price=Decimal("100.1"),
            decision_bar_close_time=now - timedelta(seconds=10),
            decision_age_seconds=10,
            atr=Decimal("1"),
            tick_size=Decimal("0.1"),
            step_size=Decimal("0.001"),
        )

    def submit_order(self, *, live_run_id: str, order_request: ExecutionOrderRequest) -> dict:
        del live_run_id
        self.submitted.append(order_request)
        assert order_request.trade_intent is not None
        quantity = float(order_request.trade_intent.target_quantity)
        return {
            "gateway_order_id": "active-contract-order-1",
            "client_order_id": "active-contract-client-1",
            "trade_ids": ["active-contract-trade-1"],
            "commissions": [],
            "gateway_status": "filled",
            "quantity": quantity,
            "filled_quantity": quantity,
            "average_fill_price": 100.1,
            "fill_timestamp": datetime.now(UTC).isoformat(),
            "fill_source": "create_order",
            "protection_order_refs": [
                {"algoId": "active-contract-stop-1", "orderType": "STOP_MARKET"},
                {"algoId": "active-contract-tp-1", "orderType": "TAKE_PROFIT_MARKET"},
            ],
        }


class ActualGatewayClient:
    def __init__(self) -> None:
        self.leverage_calls: list[tuple[int, str]] = []
        self.created_orders: list[dict] = []
        self.options: dict = {}
        self.urls = {"test": {"fapiPrivate": "https://testnet.binancefuture.com/fapi/v1"}}

    def clone(self, value):  # noqa: ANN001
        return dict(value)

    def fetch_time(self, params=None):  # noqa: ANN001
        del params
        raise RuntimeError("fake exchange uses the legacy Testnet fallback")

    def fetch_balance(self, params=None):  # noqa: ANN001
        assert params == {"type": "future"}
        return {
            "total": {"USDT": 10_000.0},
            "free": {"USDT": 10_000.0},
            "info": {"canWithdraw": False, "totalMarginBalance": "10000"},
        }

    def market(self, symbol):  # noqa: ANN001
        assert symbol == "BTC/USDT:USDT"
        return {"limits": {"amount": {"min": 0.001}}}

    def set_leverage(self, leverage, symbol):  # noqa: ANN001
        self.leverage_calls.append((leverage, symbol))
        return {"leverage": leverage, "symbol": symbol}

    def create_order(self, symbol, order_type, side, amount, price=None, params=None):  # noqa: ANN001
        self.created_orders.append(
            {
                "symbol": symbol,
                "order_type": order_type,
                "side": side,
                "amount": amount,
                "price": price,
                "params": params,
            }
        )
        if len(self.created_orders) == 1:
            return {
                "id": "active-contract-order-1",
                "clientOrderId": params.get("newClientOrderId"),
                "status": "closed",
                "average": 100.1,
                "filled": amount,
                "timestamp": 1_785_801_600_000,
            }
        return {"id": "active-contract-tp-1", "status": "open"}

    def fetch_my_trades(self, symbol, since, limit, params):  # noqa: ANN001
        del symbol, since, limit
        assert params == {"orderId": "active-contract-order-1"}
        return [{"id": "active-contract-trade-1", "fee": {"currency": "USDT", "cost": "0.01"}}]

    def fapiPrivatePostAlgoOrder(self, payload):  # noqa: N802, ANN001
        return {"algoId": "active-contract-stop-1", "status": "NEW", **payload}


class RecordingActualLegacyGateway(RecordingFilledGateway):
    def __init__(self) -> None:
        super().__init__()
        self.client = ActualGatewayClient()
        registry = Path(tempfile.gettempdir()) / f"ai-quant-legacy-gateway-{os.getpid()}.json"
        os.environ["V2_ACCOUNT_WRITER_REGISTRY_PATH"] = str(registry)
        scope = "BINANCE:TESTNET:test-legacy-gateway"
        if not registry.exists():
            bind_account(
                account_scope_key=scope,
                database_id="legacy-gateway-test-db",
                operator_identity="test",
                operator_reason="legacy gateway contract test",
            )
        lease = acquire_account_writer(
            account_scope_key=scope,
            database_id="legacy-gateway-test-db",
            owner_id=f"legacy:{os.getpid()}",
        )
        self.actual_gateway = BinanceUsdtPerpetualGateway(
            client=self.client,
            use_testnet=True,
            writer_capability=lease.capability,
        )
        self.last_result: dict | None = None

    def submit_order(self, *, live_run_id: str, order_request: ExecutionOrderRequest) -> dict:
        self.submitted.append(order_request)
        self.last_result = self.actual_gateway.submit_order(live_run_id=live_run_id, order_request=order_request)
        return self.last_result


def _force_primary_long(runtime, *, bar_time: datetime) -> None:  # noqa: ANN001
    def _primary(*, strategy, symbol, timeframe, **_kwargs) -> DecisionPipelineResult:
        del strategy, symbol, timeframe
        return DecisionPipelineResult(
            direction=TradeSide.LONG,
            should_trade=True,
            reason="primary_contract_signal",
            reference_price=Decimal("100"),
            bar_time=bar_time,
            signals=[],
            ensemble=None,
            meta_label=None,
            veto_result=None,
            confidence_multiplier=1.0,
            atr=1.0,
            volatility_context={},
            trace={
                "pipeline_status": "bet_taken",
                "strategy_lane": "directional",
                "evidence_class": "PROMOTABLE_PRIMARY",
                "meta_label_win_rate": 0.8,
                "meta_label_average_win": 0.02,
                "meta_label_average_loss": 0.01,
                "round_trip_fee_rate": 0.0002,
                "round_trip_slippage_rate": 0.0,
            },
        )

    runtime.signal_generator.decision_pipeline.evaluate = _primary


@pytest.mark.asyncio
async def test_v2_shadow_selects_legacy_writer_and_full_primary_chain_reaches_position(
    api_client, db_session, monkeypatch
) -> None:
    root = Path(__file__).resolve().parents[2]
    assert 'os.environ["AUTOMATED_TRADING_ENGINE"] = "v2_shadow"' in (
        root / "scripts/run-local-paper-scheduler.py"
    ).read_text(encoding="utf-8")
    assert '$env:AUTOMATED_TRADING_ENGINE = "v2_shadow"' in (root / "scripts/launch-paper-console.ps1").read_text(
        encoding="utf-8"
    )
    monkeypatch.setattr(settings, "binance_auto_execute", True)
    monkeypatch.setattr(settings, "binance_use_testnet", True)
    monkeypatch.setattr(settings, "live_trading_enabled", False)
    monkeypatch.setattr(settings, "automated_trading_engine", "v2_shadow")
    activation = resolve_engine_activation(settings)
    assert activation.v2_activation is EngineActivation.SHADOW
    assert activation.allow_legacy_writer is True
    jobs = resolve_scheduler_v2_jobs(activation)
    assert {"paper_runtime_cycle", "automated_trading_v2_cycle"}.issubset(jobs)

    gateway = RecordingActualLegacyGateway()
    runtime, paper_run = _runtime_without_position(db_session, gateway=gateway, mirror_to_gateway=True)
    risk_profile = RiskProfileRepository(db_session).create_profile(medium_risk_profile())
    paper_run = PaperRunRepository(db_session).update_paper_run(
        paper_run.paper_run_id or "",
        execution_profile={
            **paper_run.execution_profile,
            "risk_profile_id": risk_profile.risk_profile_id,
        },
    )
    assert paper_run is not None
    strategy = StrategyRepository(db_session).get_strategy(paper_run.strategy_id)
    assert strategy is not None
    strategy_rules = strategy.rules.model_copy(
        update={
            "position_rules": {
                "notional_usdt": 500,
                "order_notional_usdt": 600,
                "max_leverage": 3,
            },
            "takeprofit_rules": {"risk_reward": 1.5},
        }
    )
    StrategyRepository(db_session).update_strategy(strategy.strategy_id or "", StrategyUpdate(rules=strategy_rules))
    db_session.commit()
    ConfigSnapshotRepository(db_session).create_snapshot(
        ConfigSnapshot.create(
            paper_run_id=paper_run.paper_run_id or "",
            config={
                "execution_profile": paper_run.execution_profile,
                "strategy_rules": strategy_rules.model_dump(mode="json"),
            },
            created_by="test-bootstrap",
            effective_cycle_id="INITIAL",
        ),
        base_config_hash=None,
    )

    response = api_client.patch(
        f"/api/v1/execution/paper-runs/{paper_run.paper_run_id}/execution-profile",
        json={
            "execution_mode": "binance_testnet",
            "mirror_to_gateway": True,
            "cost_gate_verified": True,
            "max_leverage": 7,
            "order_notional_usdt": 123,
            "risk_per_trade": 0.05,
        },
    )
    assert response.status_code == 200
    assert response.json()["pending_config_hash"] is not None

    _store_bar(db_session, low=99, high=101, close=100, timeframe="15m")
    latest = DataRepository(db_session).get_latest_ohlcv_bar(symbol="BTC/USDT", timeframe="15m")
    assert latest is not None
    _force_primary_long(runtime, bar_time=latest.timestamp)

    cycle_request = PaperRuntimeCycleRequest(symbols=["BTC/USDT"], timeframe="15m", enable_decision_veto=False)
    registered_jobs = {}
    scheduler_payloads = []

    async def _capture_periodic(_scheduler, *, name, runner, **_kwargs):  # noqa: ANN001
        registered_jobs[name] = runner

    async def _skip_daily_review(_scheduler):  # noqa: ANN001
        return None

    monkeypatch.setattr(RuntimeScheduler, "_run_periodic", _capture_periodic)
    monkeypatch.setattr(RuntimeScheduler, "_run_daily_review_loop", _skip_daily_review)
    monkeypatch.setattr(RuntimeScheduler, "_publish_external_state", lambda _scheduler: None)
    monkeypatch.setattr("services.execution.scheduler._preload_celery_task_api", lambda: None)

    def _run_all_paper_cycles(payload):  # noqa: ANN001
        scheduler_payloads.append(payload)
        return runtime.run_cycle(
            paper_run_id=paper_run.paper_run_id or "",
            request=cycle_request,
        )

    monkeypatch.setattr(run_all_paper_runtime_cycles, "run", _run_all_paper_cycles)
    scheduler = RuntimeScheduler(coordinator=object())  # type: ignore[arg-type]
    scheduler.start()
    await asyncio.sleep(0)
    assert {"paper_runtime_cycle", "automated_trading_v2_cycle"}.issubset(registered_jobs)
    result = registered_jobs["paper_runtime_cycle"]()
    assert scheduler_payloads[0]["timeframe"] == "1m"
    assert scheduler_payloads[0]["max_symbols"] > 0
    await scheduler.stop()

    assert result.actions[0].action == "open_long", (result.actions[0].reason, gateway.last_result)
    assert result.opened_positions == 1
    assert len(gateway.submitted) == 1
    request = gateway.submitted[0]
    assert request.entry_context["requested_leverage"] == 7
    assert request.entry_context["requested_notional"] == pytest.approx(123)
    assert gateway.client.leverage_calls == [(7, "BTC/USDT:USDT")]
    assert gateway.client.created_orders[0]["params"].get("reduceOnly") is not True
    assert gateway.client.created_orders[0]["amount"] == pytest.approx(1.23)
    assert gateway.last_result is not None
    assert gateway.last_result["gateway_order_id"] == "active-contract-order-1"
    assert gateway.last_result["trade_ids"] == ["active-contract-trade-1"]
    assert {ref.get("algoId") or ref.get("id") for ref in gateway.last_result["protection_order_refs"]} == {
        "active-contract-stop-1",
        "active-contract-tp-1",
    }
    positions = runtime.execution_repo.list_latest_positions_for_run(
        run_type="paper", run_id=paper_run.paper_run_id or ""
    )
    assert len(positions) == 1
    assert positions[0].entry_price == pytest.approx(100.1)


def test_v2_shadow_legacy_sampling_stops_before_gateway_and_position_side_effects(db_session, monkeypatch) -> None:
    gateway = RecordingFilledGateway()
    runtime, paper_run = _runtime_without_position(db_session, gateway=gateway, mirror_to_gateway=True)
    PaperRunRepository(db_session).update_paper_run(
        paper_run.paper_run_id or "",
        execution_profile={
            **paper_run.execution_profile,
            "strategy_lane": "directional",
            "simulation_sampling_fallback_enabled": True,
        },
    )
    _store_bar(db_session, low=99, high=101, close=100, timeframe="15m")
    latest = DataRepository(db_session).get_latest_ohlcv_bar(symbol="BTC/USDT", timeframe="15m")
    assert latest is not None

    def _sampling(*, symbol, timeframe, primary, decision_time=None) -> DecisionPipelineResult:  # noqa: ANN001
        del symbol, timeframe, primary, decision_time
        return DecisionPipelineResult(
            direction=TradeSide.LONG,
            should_trade=True,
            reason="testnet_sampling_lane_signal",
            reference_price=Decimal("100"),
            bar_time=latest.timestamp,
            signals=[],
            ensemble=None,
            meta_label=None,
            veto_result=None,
            confidence_multiplier=1.0,
            atr=1.0,
            volatility_context={"sampling": True},
            trace={
                "pipeline_status": "testnet_sampling_signal",
                "testnet_sampling_mode": True,
                "evidence_class": "NON_PROMOTABLE_PIPELINE_SAMPLE",
            },
        )

    def _sampling_decision(*, strategy, symbol, timeframe, request, paper_run):  # noqa: ANN001
        del strategy, symbol, timeframe, request, paper_run
        return _sampling(symbol=None, timeframe=None, primary=None)

    runtime.signal_generator._decision_for_strategy = _sampling_decision
    generate_order = runtime.signal_generator.generate_order

    def _force_inconsistent_trade_flag(*args, **kwargs):  # noqa: ANN002, ANN003
        order = generate_order(*args, **kwargs)
        return order.model_copy(update={"entry_context": {**order.entry_context, "paper_order_should_trade": True}})

    runtime.signal_generator.generate_order = _force_inconsistent_trade_flag
    monkeypatch.setattr(settings, "binance_auto_execute", True)
    monkeypatch.setattr(settings, "binance_use_testnet", True)
    monkeypatch.setattr(settings, "live_trading_enabled", False)

    result = runtime.run_cycle(
        paper_run_id=paper_run.paper_run_id or "",
        request=PaperRuntimeCycleRequest(symbols=["BTC/USDT"], timeframe="15m", enable_decision_veto=True),
    )

    assert result.actions[0].action == "skip_non_promotable_sampling"
    assert result.actions[0].decision_trace["evidence_class"] == "NON_PROMOTABLE_PIPELINE_SAMPLE"
    assert gateway.submitted == []
    assert runtime.execution_repo.list_orders() == []
    assert (
        runtime.execution_repo.list_latest_positions_for_run(run_type="paper", run_id=paper_run.paper_run_id or "")
        == []
    )


def test_runtime_mismatch_fails_instead_of_remapping(monkeypatch) -> None:
    mismatched = settings.model_copy(update={"automated_trading_engine": "unsupported"})

    with pytest.raises(ValueError, match="Invalid AUTOMATED_TRADING_ENGINE"):
        resolve_engine_activation(mismatched)


def test_v2_shadow_cycle_records_decision_without_exchange_or_position_side_effects() -> None:
    adapter = build_adapter_with_successful_cycle()

    result = run_automated_trading_cycle(
        build_request(engine_activation=EngineActivation.SHADOW),
        adapter,
    )

    adapter.submit_market_order.assert_not_called()
    adapter.submit_protection.assert_not_called()
    assert result.entry_submitted is False
    assert result.position_projected is False
    assert result.protection_active is False
