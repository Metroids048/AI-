from __future__ import annotations

from services.agents import AgentTaskService
from services.strategy_library import AgentTaskRepository, ReviewRepository, StrategyRepository
from shared.models import AgentTaskRequest, StrategyCreate


class InvalidSchemaLLM:
    def generate_structured(self, *, agent_type: str, task_type: str, payload: dict) -> dict:
        return {
            "provider": "stub",
            "model": "stub-mini",
            "raw_output": {"headline": payload.get("headline"), "label": "bullish"},
        }


class TimeoutLLM:
    def generate_structured(self, *, agent_type: str, task_type: str, payload: dict) -> dict:
        raise TimeoutError("llm timeout")


def test_llm_agent_fails_closed_on_schema_validation_error(db_session) -> None:
    StrategyRepository(db_session).create_strategy(
        StrategyCreate(
            strategy_key="llm_agent_strategy",
            source="manual",
            core_thesis="llm outputs must be validated before landing",
        )
    )
    service = AgentTaskService(
        agent_repo=AgentTaskRepository(db_session),
        strategy_repo=StrategyRepository(db_session),
        review_repo=ReviewRepository(db_session),
        llm_runtime=InvalidSchemaLLM(),
    )

    task = service.submit_task(
        AgentTaskRequest(
            agent_type="news_agent",
            task_type="classify_event",
            input_payload={"headline": "ETF headline", "body": "volatility is rising"},
        )
    )

    assert task.task_status == "failed"
    assert task.output_payload["schema_validation_status"] == "failed"
    assert task.output_payload["provider_trace"]["provider"] == "stub"


def test_decision_veto_agent_applies_safe_timeout_policy(db_session) -> None:
    service = AgentTaskService(
        agent_repo=AgentTaskRepository(db_session),
        strategy_repo=StrategyRepository(db_session),
        review_repo=ReviewRepository(db_session),
        llm_runtime=TimeoutLLM(),
    )

    task = service.submit_task(
        AgentTaskRequest(
            agent_type="decision_veto_agent",
            task_type="pre_execution_veto_llm",
            input_payload={"headline": "Breaking exchange incident"},
        )
    )

    assert task.task_status == "failed"
    assert task.output_payload["safe_veto_applied"] is True
    assert task.output_payload["veto_result"]["veto"] is True
