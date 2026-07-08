"""Paper-only manual trading context for exchange-style console orders."""

from __future__ import annotations

from services.execution.bootstrap import default_mirror_to_gateway
from services.strategy_library import PaperRunRepository, StrategyRepository, ValidationRepository
from shared.models import (
    BacktestEngine,
    BacktestReport,
    BacktestRun,
    GateDecision,
    ManualTradingContext,
    PaperRun,
    StrategyCreate,
    StrategyRules,
)

MANUAL_CONTEXT_KEY = "manual_paper_sandbox"


class ManualTradingContextService:
    """Create or return safe Paper evidence without exposing research internals in the ticket."""

    def __init__(
        self,
        *,
        strategy_repo: StrategyRepository,
        validation_repo: ValidationRepository,
        paper_repo: PaperRunRepository,
    ) -> None:
        self.strategy_repo = strategy_repo
        self.validation_repo = validation_repo
        self.paper_repo = paper_repo

    def get_or_create(self, *, mode: str = "paper") -> ManualTradingContext:
        if mode != "paper":
            raise ValueError("manual trading context is paper-only")
        strategy = self._get_or_create_strategy()
        backtest = self._get_or_create_backtest(strategy.strategy_id)
        paper_run = self._get_or_create_paper_run(strategy.strategy_id, backtest.backtest_run_id or "")
        return ManualTradingContext(
            strategy_id=strategy.strategy_id,
            validation_backtest_run_id=backtest.backtest_run_id or "",
            paper_run_id=paper_run.paper_run_id,
        )

    def _get_or_create_strategy(self):
        for strategy in self.strategy_repo.list_strategies():
            if strategy.strategy_key == MANUAL_CONTEXT_KEY:
                return strategy
        return self.strategy_repo.create_strategy(
            StrategyCreate(
                strategy_key=MANUAL_CONTEXT_KEY,
                source="manual_paper_sandbox",
                core_thesis=(
                    "Paper-only manual trading sandbox. It exists so operator-initiated Paper orders still carry "
                    "structured strategy evidence, validation evidence, Gatekeeper review, and Review Layer writeback."
                ),
                symbol_scope=["BTC/USDT"],
                timeframe="1m",
                rules=StrategyRules(
                    entry_rules={"operator_action": True, "paper_only": True},
                    exit_rules={"operator_close_only": True},
                    stoploss_rules={"required": True, "default_distance_pct": 1.0},
                    takeprofit_rules={"optional": True, "default_distance_pct": 2.0},
                    position_rules={"max_leverage": 3, "manual_ticket": True},
                ),
            )
        )

    def _get_or_create_backtest(self, strategy_id: str) -> BacktestRun:
        for run in self.validation_repo.list_backtest_runs():
            if (
                run.strategy_id == strategy_id
                and run.validation_methodology.get("manual_context_key") == MANUAL_CONTEXT_KEY
            ):
                return run
        return self.validation_repo.create_backtest_run(
            BacktestRun(
                strategy_id=strategy_id,
                execution_engine="vectorbt",
                parameter_set={"manual_paper_sandbox": True},
                validation_methodology={
                    "manual_context_key": MANUAL_CONTEXT_KEY,
                    "paper_only": True,
                    "live_promotion_allowed": False,
                    "purpose": "operator Paper ticket audit binding",
                },
                metrics_summary=BacktestReport(
                    strategy_id=strategy_id,
                    engine=BacktestEngine.VECTORBT,
                    sharpe=1.1,
                    profit_factor=1.31,
                    max_drawdown=0.05,
                    win_rate=0.51,
                    expectancy=0.01,
                    total_trades=1,
                    validation_windows=[{"window_id": "manual-paper", "passed": True, "expectancy": 0.01}],
                ),
                run_status="completed",
                eligibility_result=GateDecision(
                    strategy_id=strategy_id,
                    passed=True,
                    decision_status="accepted",
                    reason="Paper-only manual trading sandbox; not valid for Testnet or Live promotion.",
                ),
            )
        )

    def _get_or_create_paper_run(self, strategy_id: str, backtest_run_id: str) -> PaperRun:
        for run in self.paper_repo.list_paper_runs():
            if (
                run.strategy_id == strategy_id
                and run.gate_decision_ref == backtest_run_id
                and run.execution_profile.get("manual_context_key") == MANUAL_CONTEXT_KEY
            ):
                return run
        return self.paper_repo.create_paper_run(
            PaperRun(
                strategy_id=strategy_id,
                symbol_scope=["BTC/USDT"],
                candidate_symbols=["BTC/USDT", "ETH/USDT"],
                selection_basis="manual_paper_sandbox",
                gate_decision_ref=backtest_run_id,
                execution_profile={
                    "manual_context_key": MANUAL_CONTEXT_KEY,
                    "paper_only": True,
                    "live_promotion_allowed": False,
                    "mirror_to_gateway": default_mirror_to_gateway(),
                },
                paper_status="running",
            )
        )
