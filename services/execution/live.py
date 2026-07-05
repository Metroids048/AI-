"""Live execution runtime over the self-owned gateway abstraction."""

from __future__ import annotations

from datetime import UTC, datetime

from services.data import DataRepository
from services.execution.gatekeeper import ExecutionGatekeeperService
from services.execution.gateway import ExchangeGateway, NullExchangeGateway
from services.review.decision_memory import DecisionMemoryService
from services.strategy_library import (
    DecisionMemoryRepository,
    ExecutionRepository,
    HypothesisRepository,
    PaperRunRepository,
    ReviewRepository,
    RiskProfileRepository,
    ValidationRepository,
)
from services.validation.admission import ValidationAdmissionService
from shared.models import (
    DecisionMemoryEntry,
    ExchangeAccountSnapshot,
    ExecutionOrderRequest,
    OrderExecution,
    ReconciliationRecord,
)


class LiveExecutionService:
    def __init__(
        self,
        *,
        data_repo: DataRepository,
        validation_repo: ValidationRepository,
        risk_profile_repo: RiskProfileRepository,
        execution_repo: ExecutionRepository,
        paper_repo: PaperRunRepository,
        review_repo: ReviewRepository,
        gateway: ExchangeGateway | None = None,
    ) -> None:
        self.data_repo = data_repo
        self.validation_repo = validation_repo
        self.risk_profile_repo = risk_profile_repo
        self.execution_repo = execution_repo
        self.paper_repo = paper_repo
        self.review_repo = review_repo
        self.gateway = gateway or NullExchangeGateway()
        self.gatekeeper = ExecutionGatekeeperService(
            data_repo=data_repo,
            validation_repo=validation_repo,
            hypothesis_repo=HypothesisRepository(review_repo.session),
            risk_profile_repo=risk_profile_repo,
            execution_repo=execution_repo,
            paper_repo=paper_repo,
            review_repo=review_repo,
        )
        self.decision_memory = DecisionMemoryService(DecisionMemoryRepository(review_repo.session))
        self.validation_admission = ValidationAdmissionService()

    def sync_account(self, *, live_run_id: str) -> ExchangeAccountSnapshot:
        snapshot = self.gateway.sync_account(live_run_id=live_run_id)
        return self.execution_repo.create_account_snapshot(snapshot)

    def submit_live_order(self, *, live_run_id: str, order_request: ExecutionOrderRequest) -> OrderExecution:
        order = self.gatekeeper.submit_order(order_request.model_copy(update={"live_run_id": live_run_id}))
        if order.execution_status != "accepted":
            self._remember(
                scope_id=live_run_id,
                decision_type="gateway_reject",
                verdict="rejected",
                summary=f"Gatekeeper rejected live order {order.symbol}",
                evidence_refs=[f"order_execution:{order.order_execution_id}"],
                context_payload={"rejection_codes": order.rejection_codes},
            )
            return order

        self._ensure_validation_admitted(order)
        gateway_result = self.gateway.submit_order(live_run_id=live_run_id, order_request=order_request)
        updated = self.execution_repo.update_order(
            order.order_execution_id or "",
            gateway_name=self.gateway.capability.gateway_name,
            gateway_order_id=gateway_result.get("gateway_order_id"),
            gateway_status=gateway_result.get("gateway_status"),
            lifecycle_history=[
                *order.lifecycle_history,
                {
                    "at": datetime.now(UTC).isoformat(),
                    "status": gateway_result.get("gateway_status"),
                    "event": "submit",
                },
            ],
            last_gateway_update_at=datetime.now(UTC),
        )
        assert updated is not None
        return updated

    def cancel_live_order(self, *, live_run_id: str, order_execution_id: str) -> OrderExecution:
        order = self.execution_repo.get_order(order_execution_id)
        if order is None or order.live_run_id != live_run_id:
            raise ValueError("live order not found")
        if not order.gateway_order_id:
            raise ValueError("live order has no gateway order id")
        gateway_result = self.gateway.cancel_order(gateway_order_id=order.gateway_order_id)
        updated = self.execution_repo.update_order(
            order_execution_id,
            gateway_status=gateway_result.get("gateway_status"),
            lifecycle_history=[
                *order.lifecycle_history,
                {
                    "at": datetime.now(UTC).isoformat(),
                    "status": gateway_result.get("gateway_status"),
                    "event": "cancel",
                },
            ],
            last_gateway_update_at=datetime.now(UTC),
        )
        assert updated is not None
        return updated

    def reconcile_live_run(self, *, live_run_id: str) -> ReconciliationRecord:
        result = self.gateway.reconcile(live_run_id=live_run_id)
        record = self.execution_repo.create_reconciliation_record(
            ReconciliationRecord(
                live_run_id=live_run_id,
                reconciliation_status=result.get("reconciliation_status", "ok"),
                open_order_count=int(result.get("open_order_count", 0)),
                position_mismatches=result.get("position_mismatches", []),
                notes=result.get("notes", []),
            )
        )
        self.execution_repo.list_orders()
        self._remember(
            scope_id=live_run_id,
            decision_type="gateway_reconcile",
            verdict=record.reconciliation_status,
            summary=f"Gateway reconciliation finished with status={record.reconciliation_status}",
            evidence_refs=[f"reconciliation:{record.reconciliation_id}"],
            context_payload={"position_mismatches": record.position_mismatches},
        )
        return record

    def _ensure_validation_admitted(self, order: OrderExecution) -> None:
        if not order.validation_backtest_run_id:
            raise ValueError("live order requires validation evidence")
        backtest = self.validation_repo.get_backtest_run(order.validation_backtest_run_id)
        if backtest is None:
            raise ValueError("validation backtest run not found")
        hypothesis_id = backtest.validation_methodology.get("hypothesis_id")
        hypothesis = (
            HypothesisRepository(self.review_repo.session).get_hypothesis(hypothesis_id)
            if hypothesis_id
            else None
        )
        gate = self.validation_admission.assess_backtest_run(run=backtest, hypothesis=hypothesis)
        if not gate.passed:
            self.execution_repo.update_order(
                order.order_execution_id or "",
                execution_status="rejected",
                rejection_reason=gate.reason,
                rejection_codes=[*order.rejection_codes, *gate.failed_thresholds],
            )
            raise ValueError(gate.reason or "validation evidence incomplete")

    def _remember(
        self,
        *,
        scope_id: str,
        decision_type: str,
        verdict: str,
        summary: str,
        evidence_refs: list[str],
        context_payload: dict,
    ) -> None:
        self.decision_memory.record_entry(
            DecisionMemoryEntry(
                scope_type="live_run",
                scope_id=scope_id,
                decision_type=decision_type,
                verdict=verdict,
                summary=summary,
                tags=["execution", "gateway"],
                evidence_refs=evidence_refs,
                context_payload=context_payload,
            )
        )
