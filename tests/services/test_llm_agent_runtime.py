from __future__ import annotations

from services.agents import AgentTaskService
from services.strategy_library import (
    AgentTaskRepository,
    LlmInvocationRepository,
    ReviewRepository,
    StrategyRepository,
)
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


class SuccessfulVetoLLM:
    def generate_structured(self, *, agent_type: str, task_type: str, payload: dict) -> dict:
        del agent_type, task_type, payload
        return {
            "provider": "stub",
            "model": "stub-mini",
            "usage": {
                "prompt_tokens": 12,
                "completion_tokens": 5,
                "total_tokens": 17,
            },
            "raw_output": {"veto": False, "veto_reason": "advisory clear"},
        }


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
    invocation = LlmInvocationRepository(db_session).list_invocations(limit=1)[0]
    assert invocation.called is True
    assert invocation.status == "timeout"
    assert invocation.error == "llm timeout"


def test_llm_invocation_persists_provider_tokens_hashes_and_latency(db_session) -> None:
    service = AgentTaskService(
        agent_repo=AgentTaskRepository(db_session),
        strategy_repo=StrategyRepository(db_session),
        review_repo=ReviewRepository(db_session),
        llm_runtime=SuccessfulVetoLLM(),
    )

    service.submit_task(
        AgentTaskRequest(
            agent_type="decision_veto_agent",
            task_type="pre_execution_veto_llm",
            input_payload={
                "cycle_id": "cycle-1",
                "decision_id": "decision-1",
                "symbol": "BTC/USDT",
            },
        )
    )

    invocation = LlmInvocationRepository(db_session).list_invocations(limit=1)[0]
    assert invocation.called is True
    assert invocation.provider == "stub"
    assert invocation.model == "stub-mini"
    assert invocation.prompt_tokens == 12
    assert invocation.completion_tokens == 5
    assert invocation.total_tokens == 17
    assert invocation.input_hash is not None
    assert invocation.output_hash is not None
    assert invocation.latency_ms is not None


class SuccessfulMarketReviewLLM:
    def generate_structured(self, *, agent_type: str, task_type: str, payload: dict) -> dict:
        del agent_type, task_type, payload
        return {
            "provider": "stub",
            "model": "stub-mini",
            "usage": {"prompt_tokens": 9, "completion_tokens": 4, "total_tokens": 13},
            "raw_output": {
                "bias": "neutral",
                "confidence": 0.4,
                "risk_flags": ["low_liquidity"],
                "summary": "range-bound BTC/ETH; advisory only",
            },
        }


def test_market_review_agent_journals_advisory_result(db_session) -> None:
    service = AgentTaskService(
        agent_repo=AgentTaskRepository(db_session),
        strategy_repo=StrategyRepository(db_session),
        review_repo=ReviewRepository(db_session),
        llm_runtime=SuccessfulMarketReviewLLM(),
    )

    task = service.submit_task(
        AgentTaskRequest(
            agent_type="review_agent",
            task_type="market_review_llm",
            input_payload={"symbols": ["BTC/USDT", "ETH/USDT"], "advisory_only": True},
        )
    )

    assert task.task_status == "completed"
    assert task.output_payload["market_review"]["bias"] == "neutral"
    assert task.output_payload["market_review"]["advisory_only"] is True
    invocation = LlmInvocationRepository(db_session).list_invocations(limit=1)[0]
    assert invocation.stage.value == "MARKET_REVIEW"
    assert invocation.called is True
    assert invocation.status == "passed"


class SuccessfulTradeReviewLLM:
    def generate_structured(self, *, agent_type: str, task_type: str, payload: dict) -> dict:
        del agent_type, task_type, payload
        return {
            "provider": "stub",
            "model": "stub-mini",
            "usage": {"prompt_tokens": 11, "completion_tokens": 6, "total_tokens": 17},
            "raw_output": {
                "bias": "support",
                "confidence": 0.72,
                "risk_flags": ["elevated_volatility"],
                "summary": "candidate aligns with ensemble; advisory only",
            },
        }


def test_trade_review_agent_journals_advisory_result(db_session) -> None:
    service = AgentTaskService(
        agent_repo=AgentTaskRepository(db_session),
        strategy_repo=StrategyRepository(db_session),
        review_repo=ReviewRepository(db_session),
        llm_runtime=SuccessfulTradeReviewLLM(),
    )

    task = service.submit_task(
        AgentTaskRequest(
            agent_type="review_agent",
            task_type="trade_review_llm",
            input_payload={
                "symbol": "BTC/USDT",
                "timeframe": "1h",
                "advisory_only": True,
            },
        )
    )

    assert task.task_status == "completed"
    assert task.output_payload["trade_review"]["bias"] == "support"
    assert task.output_payload["trade_review"]["advisory_only"] is True
    invocation = LlmInvocationRepository(db_session).list_invocations(limit=1)[0]
    assert invocation.stage.value == "TRADE_REVIEW"
    assert invocation.called is True
    assert invocation.status == "passed"
