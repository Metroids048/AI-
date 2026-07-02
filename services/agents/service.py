"""Agent-task orchestration with structured I/O only."""

from __future__ import annotations

import uuid
from pathlib import Path

from apps.api.config import settings
from research_source.worldquant_adapter import LocalAlphaScanner
from services.strategy_library import AgentTaskRepository, StrategyRepository
from shared.models import AgentTask, AgentTaskRequest

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
        return self.agent_repo.update_task(
            task.agent_task_id or "",
            output_payload=output_payload,
            task_status="completed",
            output_ref=output_payload.get("output_ref"),
        ) or task

    def _execute(self, task: AgentTask) -> dict:
        if task.agent_type == "research_agent" and task.task_type == "scan_local_alpha":
            root_path = (
                task.input_payload.get("alpha_root")
                or settings.worldquant_alpha_local_path
                or str(DEFAULT_ALPHA_ROOT)
            )
            ideas = self.alpha_scanner.scan(root_path, limit=int(task.input_payload.get("limit", 10)))
            persisted_ids: list[str] = []
            if task.input_payload.get("persist_ideas", True):
                for idea in ideas:
                    created = self.strategy_repo.create_idea(idea)
                    if created.idea_id is not None:
                        persisted_ids.append(created.idea_id)
            return {
                "alpha_root": root_path,
                "idea_count": len(ideas),
                "persisted_idea_ids": persisted_ids,
                "ideas": [idea.model_dump(mode="json") for idea in ideas],
                "output_ref": f"strategy_ideas:{len(persisted_ids)}",
            }

        return {
            "message": "task recorded but no executor is registered yet",
            "output_ref": None,
        }
