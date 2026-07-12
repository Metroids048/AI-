"""Persist Binance Demo/Testnet evidence without treating it as strategy performance."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from services.strategy_library import ExecutionRepository, PaperRunRepository, StrategyRepository, ValidationRepository
from shared.models import (
    BacktestRun,
    BinanceTestnetAccountStatus,
    ExchangeAccountSnapshot,
    GateDecision,
    LiveRun,
    OrderExecution,
    PaperRun,
    StrategyCreate,
    StrategyRules,
    TestnetAcceptanceRunResult,
    Timeframe,
    TradeSide,
)

BINANCE_DEMO_AUDIT_KEY = "binance_demo_audit_only"


@dataclass(frozen=True)
class BinanceDemoAuditContext:
    strategy_id: str
    paper_run_id: str
    live_run_id: str


class BinanceDemoAuditService:
    """Keep external fills auditable while isolating them from research strategy metrics."""

    def __init__(
        self,
        *,
        strategy_repo: StrategyRepository,
        validation_repo: ValidationRepository,
        paper_repo: PaperRunRepository,
        execution_repo: ExecutionRepository,
    ) -> None:
        self.strategy_repo = strategy_repo
        self.validation_repo = validation_repo
        self.paper_repo = paper_repo
        self.execution_repo = execution_repo

    def record_acceptance(
        self,
        *,
        acceptance_run_id: str,
        result: TestnetAcceptanceRunResult,
    ) -> list[OrderExecution]:
        context = self._context()
        records: list[OrderExecution] = []
        for evidence in result.orders:
            if (
                not evidence.gateway_order_id
                or self.execution_repo.find_order_by_gateway_order_id(evidence.gateway_order_id)
            ):
                continue
            records.append(
                self.execution_repo.create_order(
                    OrderExecution(
                        strategy_id=context.strategy_id,
                        paper_run_id=context.paper_run_id,
                        symbol=evidence.symbol,
                        direction=TradeSide.LONG,
                        execution_status="filled" if evidence.gateway_status.lower() == "filled" else "submitted",
                        stoploss_present=evidence.action == "open",
                        close_only_mode=evidence.reduce_only,
                        entry_context={
                            "execution_kind": "testnet_acceptance",
                            "acceptance_run_id": acceptance_run_id,
                            "acceptance_action": evidence.action,
                            "exchange_side": evidence.side,
                            "quantity": evidence.quantity,
                            "requested_notional": evidence.requested_notional,
                            "requested_leverage": evidence.leverage,
                            "strategy_performance_eligible": False,
                        },
                        gateway_name="binance_usdt_perpetual",
                        gateway_order_id=evidence.gateway_order_id,
                        gateway_status=evidence.gateway_status,
                        reconciliation_status="recorded",
                    )
                )
            )
        return records

    def record_recent_account_orders(self, account: BinanceTestnetAccountStatus) -> list[OrderExecution]:
        if not account.connected:
            return []
        context = self._context()
        records: list[OrderExecution] = []
        for order in account.recent_orders:
            if not order.order_id or self.execution_repo.find_order_by_gateway_order_id(order.order_id):
                continue
            side = order.side.lower()
            records.append(
                self.execution_repo.create_order(
                    OrderExecution(
                        strategy_id=context.strategy_id,
                        paper_run_id=context.paper_run_id,
                        symbol=_platform_symbol(order.symbol),
                        direction=TradeSide.LONG if side == "buy" else TradeSide.SHORT,
                        execution_status="filled" if order.status.lower() == "filled" else "submitted",
                        close_only_mode=order.reduce_only,
                        entry_context={
                            "execution_kind": "binance_demo_reconciliation",
                            "exchange_side": order.side,
                            "order_type": order.order_type,
                            "quantity": order.quantity,
                            "actual_avg_price": order.avg_price,
                            "exchange_update_time": order.update_time,
                            "strategy_performance_eligible": False,
                        },
                        gateway_name="binance_usdt_perpetual",
                        gateway_order_id=order.order_id,
                        gateway_status=order.status.lower(),
                        reconciliation_status="imported",
                    )
                )
            )
        return records

    def record_account_snapshot(self, account: BinanceTestnetAccountStatus) -> ExchangeAccountSnapshot | None:
        if not account.connected or account.wallet_balance is None or account.available_balance is None:
            return None
        context = self._context()
        snapshot_time = account.synced_at or datetime.now(UTC)
        snapshots = self.execution_repo.list_account_snapshots(live_run_id=context.live_run_id)
        latest = snapshots[-1] if snapshots else None
        if (
            latest is not None
            and latest.wallet_balance == float(account.wallet_balance)
            and latest.available_balance == float(account.available_balance)
            and latest.unrealized_pnl == float(account.unrealized_pnl or 0.0)
            and latest.open_position_count == int(account.open_position_count or 0)
            and latest.snapshot_time is not None
            and snapshot_time - latest.snapshot_time < timedelta(minutes=5)
        ):
            return latest
        return self.execution_repo.create_account_snapshot(
            ExchangeAccountSnapshot(
                live_run_id=context.live_run_id,
                exchange="binance",
                wallet_balance=float(account.wallet_balance),
                available_balance=float(account.available_balance),
                margin_balance=float(account.wallet_balance),
                unrealized_pnl=float(account.unrealized_pnl or 0.0),
                open_position_count=int(account.open_position_count or 0),
                source_ref="binance_demo_testnet_api",
                snapshot_time=snapshot_time,
            )
        )

    def record_exchange_positions(self, account: BinanceTestnetAccountStatus) -> int:
        """Mirror Binance Demo positions into local Paper runs so desk + risk share one truth."""
        if not account.connected or account.error:
            return 0
        # A connected-but-empty payload right after process start can be a false flat.
        # Only clear local opens when the probe also returned a wallet snapshot.
        if not account.positions and account.wallet_balance is None:
            return 0
        from shared.models import PositionSnapshot

        context = self._context()
        snapshot_time = account.synced_at or datetime.now(UTC)
        exchange_by_symbol = {
            _platform_symbol(position.symbol): position for position in account.positions if abs(position.quantity) > 0
        }
        target_run_ids = {context.paper_run_id}
        strategy_run_ids: set[str] = set()
        # Audit run gets a full exchange mirror. Strategy/mature runs only get
        # ghost cleanup — injecting Demo positions into them inflated portfolio
        # risk and blocked directional opens (portfolio_initial_risk_exceeded).
        for run in self.paper_repo.list_paper_runs():
            run_id = run.paper_run_id or ""
            if not run_id or run_id == context.paper_run_id:
                continue
            profile = run.execution_profile or {}
            armed_mature = profile.get("auto_paper_runtime_key") == "auto_paper_mature_templates" and (
                profile.get("cost_gate_verified")
                or profile.get("mirror_to_gateway")
                or profile.get("execution_mode") == "binance_simulation_first"
            )
            has_open = any(
                abs(float(item.quantity)) > 0
                for item in self.execution_repo.list_latest_positions_for_run(
                    run_type="paper",
                    run_id=run_id,
                )
            )
            if armed_mature or has_open:
                strategy_run_ids.add(run_id)

        written = 0
        for run_id in target_run_ids | strategy_run_ids:
            latest = {
                item.symbol: item
                for item in self.execution_repo.list_latest_positions_for_run(
                    run_type="paper",
                    run_id=run_id,
                    include_closed=True,
                )
            }
            is_audit_run = run_id == context.paper_run_id
            strategy_owned: set[str] = set()
            if not is_audit_run:
                for symbol, existing in latest.items():
                    if abs(float(existing.quantity)) <= 0:
                        continue
                    entry = self.execution_repo.find_latest_filled_entry_order(
                        run_type="paper",
                        run_id=run_id,
                        symbol=symbol,
                    )
                    if entry is not None and entry.gateway_order_id:
                        strategy_owned.add(symbol)
            seen: set[str] = set()
            if is_audit_run:
                for symbol, position in exchange_by_symbol.items():
                    seen.add(symbol)
                    side = TradeSide.LONG if str(position.side).lower() in {"long", "buy"} else TradeSide.SHORT
                    self.execution_repo.create_position_snapshot(
                        PositionSnapshot(
                            run_type="paper",
                            run_id=run_id,
                            symbol=symbol,
                            side=side,
                            quantity=float(position.quantity),
                            entry_price=float(position.entry_price),
                            mark_price=float(position.mark_price or position.entry_price),
                            unrealized_pnl=float(position.unrealized_pnl or 0.0),
                            snapshot_time=snapshot_time,
                        )
                    )
                    written += 1
            for symbol, existing in latest.items():
                if abs(float(existing.quantity)) <= 0:
                    continue
                if is_audit_run:
                    if symbol in seen:
                        continue
                elif symbol in strategy_owned and symbol in exchange_by_symbol:
                    # Real strategy fill still open on exchange — keep.
                    continue
                self.execution_repo.create_position_snapshot(
                    PositionSnapshot(
                        run_type="paper",
                        run_id=run_id,
                        symbol=symbol,
                        side=existing.side,
                        quantity=0.0,
                        entry_price=float(existing.entry_price),
                        mark_price=float(existing.mark_price or existing.entry_price),
                        unrealized_pnl=0.0,
                        snapshot_time=snapshot_time,
                    )
                )
                written += 1
        return written

    def _context(self) -> BinanceDemoAuditContext:
        strategy = next(
            (item for item in self.strategy_repo.list_strategies() if item.strategy_key == BINANCE_DEMO_AUDIT_KEY),
            None,
        )
        if strategy is None:
            strategy = self.strategy_repo.create_strategy(
                StrategyCreate(
                    strategy_key=BINANCE_DEMO_AUDIT_KEY,
                    source="platform:binance_demo_reconciliation",
                    core_thesis="Audit-only record of Binance Demo/Testnet orders; never used as strategy performance.",
                    symbol_scope=["BTC/USDT"],
                    timeframe=Timeframe.M1,
                    rules=StrategyRules(
                        entry_rules={"audit_only": True},
                        exit_rules={"audit_only": True},
                        stoploss_rules={"not_applicable": True},
                        takeprofit_rules={"not_applicable": True},
                        position_rules={"strategy_performance_eligible": False},
                    ),
                )
            )
        backtest = next(
            (
                item
                for item in self.validation_repo.list_backtest_runs()
                if item.strategy_id == strategy.strategy_id
                and item.validation_methodology.get("audit_context_key") == BINANCE_DEMO_AUDIT_KEY
            ),
            None,
        )
        if backtest is None:
            backtest = self.validation_repo.create_backtest_run(
                BacktestRun(
                    strategy_id=strategy.strategy_id or "",
                    execution_engine="audit",
                    parameter_set={"audit_only": True},
                    validation_methodology={"audit_context_key": BINANCE_DEMO_AUDIT_KEY},
                    run_status="completed",
                    eligibility_result=GateDecision(
                        strategy_id=strategy.strategy_id or "",
                        passed=False,
                        decision_status="rejected_with_reason",
                        reason="Binance Demo reconciliation records are not executable strategy evidence.",
                    ),
                )
            )
        paper_run = next(
            (
                item
                for item in self.paper_repo.list_paper_runs()
                if item.strategy_id == strategy.strategy_id
                and item.execution_profile.get("audit_context_key") == BINANCE_DEMO_AUDIT_KEY
            ),
            None,
        )
        if paper_run is None:
            paper_run = self.paper_repo.create_paper_run(
                PaperRun(
                    strategy_id=strategy.strategy_id or "",
                    symbol_scope=[],
                    candidate_symbols=[],
                    selection_basis="binance_demo_audit_only",
                    gate_decision_ref=backtest.backtest_run_id,
                    execution_profile={
                        "audit_context_key": BINANCE_DEMO_AUDIT_KEY,
                        "strategy_performance_eligible": False,
                        "mirror_to_gateway": False,
                    },
                    paper_status="paused",
                )
            )
        live_run = next(
            (
                item
                for item in self.execution_repo.list_live_runs()
                if item.strategy_id == strategy.strategy_id
                and item.live_metrics_summary.get("audit_context_key") == BINANCE_DEMO_AUDIT_KEY
            ),
            None,
        )
        if live_run is None:
            live_run = self.execution_repo.create_live_run(
                LiveRun(
                    strategy_id=strategy.strategy_id or "",
                    capital_tier="testnet_audit",
                    live_status="audit_only",
                    validation_backtest_run_id=backtest.backtest_run_id,
                    live_metrics_summary={"audit_context_key": BINANCE_DEMO_AUDIT_KEY},
                )
            )
        return BinanceDemoAuditContext(
            strategy_id=strategy.strategy_id or "",
            paper_run_id=paper_run.paper_run_id or "",
            live_run_id=live_run.live_run_id or "",
        )


def _platform_symbol(symbol: str) -> str:
    normalized = symbol.replace(":USDT", "").replace("/", "")
    if normalized == "1000PEPEUSDT":
        return "PEPE/USDT"
    if normalized.endswith("USDT"):
        return f"{normalized[:-4]}/USDT"
    return symbol
