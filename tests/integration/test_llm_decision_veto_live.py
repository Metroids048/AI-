from __future__ import annotations

import os

import pytest

from services.agents import AgentTaskService, build_configured_llm_runtime
from services.strategy_library import AgentTaskRepository, ReviewRepository, StrategyRepository
from shared.config import settings
from shared.models import AgentTaskRequest


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("RUN_LLM_VETO_INTEGRATION") != "1",
    reason="live LLM decision-veto smoke is opt-in",
)
def test_decision_veto_agent_reaches_a_real_llm_provider(db_session) -> None:
    """End-to-end check that the LLM veto chain is actually reachable.

    Section 0 diagnosis found the chain silently never running (no configured
    provider -> UnavailableLLMRuntime, zero logs). This exercises the full
    submit_task -> _execute_llm_veto -> AgentTaskRepository path against a
    real provider (OpenRouter free tier) instead of a stub, so a regression
    in the fallback chain or the persisted agent_tasks row shows up here.
    """
    assert settings.openrouter_api_key or settings.github_models_token, (
        "RUN_LLM_VETO_INTEGRATION=1 requires at least one free-tier key configured in .env"
    )

    service = AgentTaskService(
        agent_repo=AgentTaskRepository(db_session),
        strategy_repo=StrategyRepository(db_session),
        review_repo=ReviewRepository(db_session),
        llm_runtime=build_configured_llm_runtime(),
    )

    task = service.submit_task(
        AgentTaskRequest(
            agent_type="decision_veto_agent",
            task_type="pre_execution_veto_llm",
            input_payload={
                "strategy": {
                    "core_thesis": "BTC funding carry, long when funding negative and basis compresses",
                    "entry_rules": {"funding_threshold_bps": 0.5},
                },
                "symbol": "BTC/USDT",
                "signal": {"direction": "long", "confidence": 0.6},
            },
        )
    )

    assert task.output_payload["schema_validation_status"] == "passed"
    assert task.output_payload["provider_trace"]["provider"] in {"anthropic", "openrouter", "github_models"}
    assert isinstance(task.output_payload["veto_result"]["veto"], bool)
    assert isinstance(task.output_payload["veto_result"]["veto_reason"], str)

    persisted = AgentTaskRepository(db_session).get_task(task.agent_task_id or "")
    assert persisted is not None
    assert persisted.agent_type == "decision_veto_agent"
    assert persisted.task_status == "completed"
