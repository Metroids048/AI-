"""Agent task API backed by the structured agent-task service."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from apps.api.http import collection_response, not_found
from services.agents import AgentTaskService, build_configured_llm_runtime
from services.database import get_db_session
from services.strategy_library import AgentTaskRepository, ReviewRepository, StrategyRepository
from shared.config import settings
from shared.models import (
    AgentTask,
    AgentTaskRequest,
    CollectionResponse,
    LLMProviderStatus,
    LLMStatusResponse,
    TaskSubmission,
)

router = APIRouter(prefix="/agents", tags=["agents"])


def _service(db: Session) -> AgentTaskService:
    return AgentTaskService(
        agent_repo=AgentTaskRepository(db),
        strategy_repo=StrategyRepository(db),
        review_repo=ReviewRepository(db),
        llm_runtime=build_configured_llm_runtime(),
    )


@router.get("/tasks", response_model=CollectionResponse[AgentTask])
def list_agent_tasks(
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db_session),
) -> CollectionResponse[AgentTask]:
    return collection_response(_service(db).list_tasks(limit=limit))


@router.post("/tasks", response_model=TaskSubmission, status_code=status.HTTP_202_ACCEPTED)
def submit_agent_task(body: AgentTaskRequest, db: Session = Depends(get_db_session)) -> TaskSubmission:
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


@router.get("/llm-status", response_model=LLMStatusResponse)
def get_llm_status(
    recent_limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db_session),
) -> LLMStatusResponse:
    """Diagnostic view of the LLM fallback chain, without ever echoing key values.

    Added because a misconfigured/empty provider chain previously failed
    completely silently (no logs, no API surface) -> decision_veto_agent
    tasks never ran and nobody could tell why.
    """
    providers = [
        LLMProviderStatus(
            provider="anthropic",
            configured=bool(settings.claude_api_key),
            detail="CLAUDE_API_KEY / ANTHROPIC_API_KEY" if settings.claude_api_key else "not configured",
        ),
        LLMProviderStatus(
            provider="openrouter",
            configured=bool(settings.openrouter_api_key),
            detail="OPENROUTER_API_KEY" if settings.openrouter_api_key else "not configured",
        ),
        LLMProviderStatus(
            provider="github_models",
            configured=bool(settings.github_models_token),
            detail="GITHUB_MODELS_TOKEN" if settings.github_models_token else "not configured",
        ),
    ]
    agent_repo = AgentTaskRepository(db)
    recent_tasks = agent_repo.list_tasks_by_agent_type(agent_type="decision_veto_agent", limit=recent_limit)
    failure_count = sum(1 for task in recent_tasks if task.task_status == "failed")
    return LLMStatusResponse(
        providers=providers,
        any_provider_configured=any(item.configured for item in providers),
        recent_decision_veto_tasks=recent_tasks,
        recent_decision_veto_failure_count=failure_count,
    )
