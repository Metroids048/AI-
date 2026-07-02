"""Agent task API backed by the structured agent-task service."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from apps.api.http import collection_response, not_found
from services.agents import AgentTaskService
from services.database import get_db_session
from services.strategy_library import AgentTaskRepository, StrategyRepository
from shared.models import AgentTask, AgentTaskRequest, CollectionResponse, TaskSubmission

router = APIRouter(prefix="/agents", tags=["agents"])


def _service(db: Session) -> AgentTaskService:
    return AgentTaskService(
        agent_repo=AgentTaskRepository(db),
        strategy_repo=StrategyRepository(db),
    )


@router.get("/tasks", response_model=CollectionResponse[AgentTask])
def list_agent_tasks(db: Session = Depends(get_db_session)) -> CollectionResponse[AgentTask]:
    return collection_response(_service(db).list_tasks())


@router.post("/tasks", response_model=TaskSubmission, status_code=status.HTTP_202_ACCEPTED)
def submit_agent_task(
    body: AgentTaskRequest, db: Session = Depends(get_db_session)
) -> TaskSubmission:
    task = _service(db).submit_task(body)
    return TaskSubmission(
        task_id=task.agent_task_id,
        resource_type="agent_task",
        resource_id=task.agent_task_id,
        detail={"task_status": task.task_status},
    )


@router.get("/tasks/{agent_task_id}", response_model=AgentTask)
def get_agent_task(agent_task_id: str, db: Session = Depends(get_db_session)) -> AgentTask:
    task = _service(db).get_task(agent_task_id)
    if task is None:
        raise not_found("agent_task", agent_task_id)
    return task
