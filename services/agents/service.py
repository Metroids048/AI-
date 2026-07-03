"""Agent-task orchestration with structured I/O only."""

from __future__ import annotations

import uuid
from pathlib import Path

from apps.api.config import settings
from research_source.worldquant_adapter import LocalAlphaScanner
from services.strategy_library import AgentTaskRepository, StrategyRepository
from shared.models import AgentTask, AgentTaskRequest, DecisionVetoResult

DEFAULT_ALPHA_ROOT = Path(r"C:\Users\Windows11\Desktop\alpha")


class AgentTaskService:
    """Execute the first structured agent tasks over persisted repository seams."""

    def __init__(
        self,
        *,
        agent_repo: AgentTaskRepository,
        strategy_repo: StrategyRepository,
    ) -> None:
        self.agent_repo = agent_repo
        self.strategy_repo = strategy_repo
        self.alpha_scanner = LocalAlphaScanner()

    def list_tasks(self) -> list[AgentTask]:
        return self.agent_repo.list_tasks()

    def get_task(self, agent_task_id: str) -> AgentTask | None:
        return self.agent_repo.get_task(agent_task_id)

    def submit_task(self, request: AgentTaskRequest) -> AgentTask:
        task = self.agent_repo.create_task(
            AgentTask(
                agent_task_id=str(uuid.uuid4()),
                agent_type=request.agent_type,
                task_type=request.task_type,
                input_ref=request.input_ref,
                input_payload=request.input_payload,
                priority=request.priority,
                task_status="running",
            )
        )
        output_payload = self._execute(task)
        completed = output_payload.get("executor_registered", True)
        return (
            self.agent_repo.update_task(
                task.agent_task_id or "",
                output_payload=output_payload,
                task_status="completed" if completed else "failed",
                error_summary=None if completed else output_payload.get("message"),
                output_ref=output_payload.get("output_ref"),
            )
            or task
        )

    def _execute(self, task: AgentTask) -> dict:
        if task.agent_type == "research_agent" and task.task_type == "scan_local_alpha":
            root_path = (
                task.input_payload.get("alpha_root") or settings.worldquant_alpha_local_path or str(DEFAULT_ALPHA_ROOT)
            )
            ideas = self.alpha_scanner.scan(root_path, limit=int(task.input_payload.get("limit", 10)))
            persisted_ids: list[str] = []
            if task.input_payload.get("persist_ideas", True):
                for idea in ideas:
                    created = self.strategy_repo.create_idea(idea)
                    if created.idea_id is not None:
                        persisted_ids.append(created.idea_id)
            return {
                "executor_registered": True,
                "alpha_root": root_path,
                "idea_count": len(ideas),
                "persisted_idea_ids": persisted_ids,
                "ideas": [idea.model_dump(mode="json") for idea in ideas],
                "output_ref": f"strategy_ideas:{len(persisted_ids)}",
            }

        if task.agent_type == "decision_veto_agent" and task.task_type == "pre_execution_veto":
            risk_events = task.input_payload.get("risk_events", [])
            high_risk_events = [
                event for event in risk_events if str(event.get("severity", "")).lower() in {"high", "critical"}
            ]
            forced_reason = task.input_payload.get("forced_veto_reason")
            result = DecisionVetoResult(
                veto=bool(high_risk_events or forced_reason),
                veto_reason=forced_reason
                or (
                    "high severity risk event present"
                    if high_risk_events
                    else "no blocking risk evidence in structured payload"
                ),
                agent_task_ref=task.agent_task_id,
            )
            return {
                "executor_registered": True,
                "veto_result": result.model_dump(mode="json"),
                "risk_event_count": len(risk_events),
                "high_risk_event_count": len(high_risk_events),
                "output_ref": f"decision_veto:{task.agent_task_id}",
            }

        if task.agent_type == "review_agent" and task.task_type == "summarize_failures":
            failures = task.input_payload.get("failures", [])
            failure_types = sorted({str(item.get("failure_type", "unknown")) for item in failures})
            return {
                "executor_registered": True,
                "failure_count": len(failures),
                "failure_patterns": failure_types,
                "recommendations": [
                    "review repeated failure patterns before changing strategy parameters",
                    "do not promote strategies without validation evidence",
                ],
                "output_ref": f"review_summary:{task.agent_task_id}",
            }

        return {
            "executor_registered": False,
            "message": "task recorded but no executor is registered yet",
            "output_ref": None,
        }
