"""Autonomous paper-runtime cycles over validation-admitted strategies."""

from __future__ import annotations

from datetime import UTC, datetime

from services.data import DataRepository
from services.execution.gatekeeper import ExecutionGatekeeperService
from services.execution.gateway import ExchangeGateway
from services.execution.paper_cycle_orchestrator import PaperCycleOrchestrator
from services.execution.paper_exchange_execution import PaperExchangeExecutionService
from services.execution.paper_order_lifecycle import PaperOrderLifecycleService
from services.execution.paper_signal import PaperSignalGenerator
from services.strategy_library import (
    AgentTaskRepository,
    DecisionSnapshotRepository,
    ExecutionRepository,
    NotificationRepository,
    PaperRunRepository,
    ReviewRepository,
    StrategyRepository,
)
from shared.models import (
    PaperRun,
    PaperRuntimeCycleRequest,
    PaperRuntimeCycleResult,
    PaperRuntimeStatus,
)


class PaperRuntimeService:
    """Public gateway for paper-runtime cycles — delegates to PaperCycleOrchestrator."""

    def __init__(
        self,
        *,
        data_repo: DataRepository,
        execution_repo: ExecutionRepository,
        paper_repo: PaperRunRepository,
        strategy_repo: StrategyRepository,
        agent_repo: AgentTaskRepository | None = None,
        review_repo: ReviewRepository | None = None,
        notification_repo: NotificationRepository | None = None,
        gatekeeper: ExecutionGatekeeperService,
        gateway: ExchangeGateway | None = None,
    ) -> None:
        self.data_repo = data_repo
        self.execution_repo = execution_repo
        self.paper_repo = paper_repo
        self.strategy_repo = strategy_repo
        self.review_repo = review_repo
        self.gatekeeper = gatekeeper
        self.gateway = gateway
        exchange_execution = PaperExchangeExecutionService(
            execution_repo=execution_repo,
            gateway=gateway,
            review_repo=review_repo,
        )
        order_lifecycle = PaperOrderLifecycleService(execution_repo=execution_repo)
        decision_snapshot_repo = DecisionSnapshotRepository(execution_repo.session)
        signal_generator = PaperSignalGenerator(
            data_repo=data_repo,
            execution_repo=execution_repo,
            agent_repo=agent_repo,
            strategy_repo=strategy_repo,
            review_repo=review_repo,
            notification_repo=notification_repo,
        )
        self.cycle_orchestrator = PaperCycleOrchestrator(
            data_repo=data_repo,
            execution_repo=execution_repo,
            paper_repo=paper_repo,
            strategy_repo=strategy_repo,
            gatekeeper=gatekeeper,
            gateway=gateway,
            exchange_execution=exchange_execution,
            order_lifecycle=order_lifecycle,
            signal_generator=signal_generator,
            decision_snapshot_repo=decision_snapshot_repo,
            review_repo=review_repo,
        )
        # Preserve accessible service references for external callers
        self.exchange_execution = exchange_execution
        self.order_lifecycle = order_lifecycle
        self.decision_snapshot_repo = decision_snapshot_repo
        self.signal_generator = signal_generator

    def get_runtime_status(self, *, paper_run_id: str) -> PaperRuntimeStatus:
        paper_run = self._require_paper_run(paper_run_id)
        positions = self.execution_repo.list_latest_positions_for_run(
            run_type="paper",
            run_id=paper_run_id,
        )
        metrics = dict(paper_run.paper_metrics_summary)
        return PaperRuntimeStatus(
            paper_run_id=paper_run_id,
            paper_status=paper_run.paper_status,
            candidate_symbols=paper_run.candidate_symbols,
            open_position_symbols=sorted(position.symbol for position in positions),
            account_equity=float(metrics.get("account_equity", self._starting_equity(paper_run))),
            last_cycle_at=_parse_datetime(metrics.get("last_cycle_at")),
            last_scanned_symbols=list(metrics.get("last_scanned_symbols", [])),
            last_action_counts=dict(metrics.get("last_action_counts", {})),
            last_cycle_decisions=list(metrics.get("last_cycle_decisions", [])),
        )

    def run_cycle(self, *, paper_run_id: str, request: PaperRuntimeCycleRequest) -> PaperRuntimeCycleResult:
        return self.cycle_orchestrator.run_cycle(paper_run_id=paper_run_id, request=request)

    def _require_paper_run(self, paper_run_id: str) -> PaperRun:
        paper_run = self.paper_repo.get_paper_run(paper_run_id)
        if paper_run is None:
            raise ValueError("paper run not found")
        return paper_run

    @staticmethod
    def _starting_equity(paper_run: PaperRun) -> float:
        return float(
            paper_run.paper_metrics_summary.get("account_equity")
            or paper_run.execution_profile.get("account_equity")
            or 10_000.0
        )

    @staticmethod
    def _initial_equity(paper_run: PaperRun) -> float:
        return float(paper_run.execution_profile.get("account_equity") or 10_000.0)


def _parse_datetime(value: object) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str):
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    return None
