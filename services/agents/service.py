"""Agent-task orchestration with structured I/O only."""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from collections.abc import Callable

from research_source.open_source_strategy_library import OpenSourceStrategyExtractor, OpenSourceStrategyLibrary
from research_source.worldquant_adapter import LocalAlphaScanner
from services.agents.llm_runtime import StructuredLLMRuntime, UnavailableLLMRuntime
from services.research.integrations.research_council import ResearchCouncil
from services.strategy_library import (
    AgentTaskRepository,
    LlmInvocationRepository,
    ReviewRepository,
    StrategyRepository,
)
from shared.config import settings
from shared.models import (
    AgentTask,
    AgentTaskRequest,
    DecisionVetoResult,
    FailureRecord,
    LlmInvocation,
    LlmInvocationStage,
    RiskLevel,
    StrategyDraft,
    StrategyIdea,
    StrategyRules,
    Timeframe,
)

logger = logging.getLogger(__name__)


class AgentTaskService:
    """Execute the first structured agent tasks over persisted repository seams."""

    def __init__(
        self,
        *,
        agent_repo: AgentTaskRepository,
        strategy_repo: StrategyRepository,
        review_repo: ReviewRepository | None = None,
        llm_runtime: StructuredLLMRuntime | None = None,
    ) -> None:
        self.agent_repo = agent_repo
        self.strategy_repo = strategy_repo
        self.review_repo = review_repo
        self.llm_runtime = llm_runtime or UnavailableLLMRuntime()
        self.alpha_scanner = LocalAlphaScanner()
        self.open_source_library = OpenSourceStrategyLibrary()
        self.open_source_extractor = OpenSourceStrategyExtractor()
        self.research_council = ResearchCouncil(llm_runtime=self.llm_runtime)
        # Executor registry — replaces the legacy if/elif dispatch chain.
        # New agent executors are registered via register_executor() without
        # modifying _execute(). Keys are (agent_type, task_type) tuples.
        self._executors: dict[tuple[str, str], Callable[[AgentTask], dict]] = {}
        self._register_default_executors()

    def register_executor(self, agent_type: str, task_type: str, handler: Callable[[AgentTask], dict]) -> None:
        """Register a new agent-task executor (hot-pluggable).

        This is the extension point for Coding/Backtest/Optimization/Risk agents
        that currently have no executor. External modules can register handlers
        without modifying this service.
        """
        self._executors[(agent_type, task_type)] = handler

    def _register_default_executors(self) -> None:
        self._executors[("research_agent", "scan_local_alpha")] = self._handle_scan_local_alpha
        self._executors[("research_agent", "import_open_source_sources")] = self._handle_import_open_source
        self._executors[("research_agent", "extract_open_source_strategy_ideas")] = (
            self._handle_extract_open_source_ideas
        )
        self._executors[("strategy_agent", "materialize_seed_strategy_drafts")] = self._handle_materialize_drafts
        self._executors[("decision_veto_agent", "pre_execution_veto")] = self._handle_deterministic_veto
        self._executors[("review_agent", "summarize_failures")] = self._handle_summarize_failures
        self._executors[("research_agent", "research_council_review")] = self._handle_research_council_review

    def list_tasks(self, *, limit: int = 50) -> list[AgentTask]:
        return self.agent_repo.list_tasks(limit=limit)

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
                attempt_history=[],
            )
        )
        started_at = time.perf_counter()
        output_payload = self._execute(task)
        latency_ms = max(0, int((time.perf_counter() - started_at) * 1000))
        if task.task_type in {"pre_execution_veto_llm", "market_review_llm", "trade_review_llm"}:
            provider_trace = output_payload.get("provider_trace") or {}
            usage = output_payload.get("usage") or {}
            serialized_input = json.dumps(task.input_payload, sort_keys=True, default=str)
            serialized_output = json.dumps(output_payload, sort_keys=True, default=str)
            LlmInvocationRepository(self.agent_repo.session).create_invocation(
                LlmInvocation(
                    cycle_id=str(task.input_payload.get("cycle_id") or "") or None,
                    decision_id=str(task.input_payload.get("decision_id") or "") or None,
                    symbol=str(task.input_payload.get("symbol") or "") or None,
                    called=True,
                    provider=provider_trace.get("provider"),
                    model=provider_trace.get("model"),
                    stage=(
                        LlmInvocationStage.MARKET_REVIEW
                        if task.task_type == "market_review_llm"
                        else LlmInvocationStage.TRADE_REVIEW
                    ),
                    status=str(output_payload.get("schema_validation_status") or "unknown"),
                    input_hash=hashlib.sha256(serialized_input.encode()).hexdigest(),
                    output_hash=hashlib.sha256(serialized_output.encode()).hexdigest(),
                    latency_ms=latency_ms,
                    prompt_tokens=int(usage.get("prompt_tokens") or 0),
                    completion_tokens=int(usage.get("completion_tokens") or 0),
                    total_tokens=int(usage.get("total_tokens") or 0),
                    error=output_payload.get("message"),
                )
            )
        completed = output_payload.get("completed", output_payload.get("executor_registered", True))
        task_status = output_payload.get("task_status", "completed" if completed else "failed")
        return (
            self.agent_repo.update_task(
                task.agent_task_id or "",
                output_payload=output_payload,
                task_status=task_status,
                error_summary=None if task_status == "completed" else output_payload.get("message"),
                output_ref=output_payload.get("output_ref"),
                executor_name=output_payload.get("executor_name"),
                attempt_history=output_payload.get("attempt_history", []),
                provider_trace=output_payload.get("provider_trace", {}),
                schema_validation_status=output_payload.get("schema_validation_status"),
            )
            or task
        )

    def _execute(self, task: AgentTask) -> dict:
        # LLM-backed tasks with multi-agent routing (classify_event spans
        # news_agent / twitter_agent / telegram_agent, so they cannot live in
        # the (agent_type, task_type) registry without duplication).
        if task.task_type == "classify_event" and task.agent_type in {
            "news_agent",
            "twitter_agent",
            "telegram_agent",
        }:
            return self._execute_llm_classification(task)
        if task.agent_type == "decision_veto_agent" and task.task_type == "pre_execution_veto_llm":
            return self._execute_llm_veto(task)
        if task.agent_type == "review_agent" and task.task_type == "market_review_llm":
            return self._execute_llm_market_review(task)
        if task.agent_type == "review_agent" and task.task_type == "trade_review_llm":
            return self._execute_llm_trade_review(task)
        # Registry dispatch — O(1) lookup instead of an if/elif chain.
        handler = self._executors.get((task.agent_type, task.task_type))
        if handler is None:
            return {
                "executor_registered": False,
                "completed": False,
                "message": "task recorded but no executor is registered yet",
                "output_ref": None,
            }
        return handler(task)

    # ------------------------------------------------------------------ #
    # Registered executors — each was a branch of the legacy _execute()   #
    # if/elif chain. Extracted as methods so new executors can be added   #
    # via register_executor() without touching dispatch logic.            #
    # ------------------------------------------------------------------ #

    def _handle_scan_local_alpha(self, task: AgentTask) -> dict:
        root_path = task.input_payload.get("alpha_root") or settings.worldquant_alpha_local_path
        if not root_path:
            return {
                "executor_registered": True,
                "completed": False,
                "task_status": "failed",
                "message": "alpha_root is required",
                "output_ref": "strategy_ideas:0",
            }
        ideas = self.alpha_scanner.scan(root_path, limit=int(task.input_payload.get("limit", 10)))
        persisted_ids: list[str] = []
        if task.input_payload.get("persist_ideas", True):
            for idea in ideas:
                created = self.strategy_repo.create_idea(idea)
                if created.idea_id is not None:
                    persisted_ids.append(created.idea_id)
                    self._record_alpha_rejection_if_needed(created)
        return {
            "executor_registered": True,
            "alpha_root": root_path,
            "idea_count": len(ideas),
            "persisted_idea_ids": persisted_ids,
            "ideas": [idea.model_dump(mode="json") for idea in ideas],
            "output_ref": f"strategy_ideas:{len(persisted_ids)}",
        }

    def _handle_import_open_source(self, task: AgentTask) -> dict:
        import_result = self.open_source_library.import_sources(
            source_ids=list(task.input_payload.get("source_ids", [])),
            refresh_assets=bool(task.input_payload.get("refresh_assets", True)),
            fetch_remote=bool(task.input_payload.get("fetch_remote", False)),
        )
        return {
            "executor_registered": True,
            "imported_count": len(import_result.imported),
            "failed_count": len(import_result.failed),
            "asset_count": len(import_result.imported_assets),
            "failed_asset_count": len(import_result.failed_assets),
            "imported_source_ids": [item.source_id for item in import_result.imported],
            "failed_source_ids": [item.source_id for item in import_result.failed],
            "imported_assets": [item.model_dump(mode="json") for item in import_result.imported_assets],
            "failed_assets": [item.model_dump(mode="json") for item in import_result.failed_assets],
            "output_ref": f"open_source_sources:{len(import_result.imported)}",
        }

    def _handle_extract_open_source_ideas(self, task: AgentTask) -> dict:
        source_ids = list(task.input_payload.get("source_ids", []))
        max_ideas_per_source = task.input_payload.get("max_ideas_per_source")
        persist_ideas = bool(task.input_payload.get("persist_ideas", True))
        manifests = (
            [source for source in self.open_source_library.list_sources() if source.source_id in set(source_ids)]
            if source_ids
            else self.open_source_library.list_sources()
        )
        open_source_ideas: list[StrategyIdea] = []
        open_source_persisted_ids: list[str] = []
        for manifest in manifests:
            open_source_ideas.extend(
                self.open_source_extractor.extract_ideas(
                    manifest,
                    max_ideas=int(max_ideas_per_source) if max_ideas_per_source is not None else None,
                )
            )
        if persist_ideas:
            for idea in open_source_ideas:
                created = self.strategy_repo.create_idea(idea)
                if created.idea_id is not None:
                    open_source_persisted_ids.append(created.idea_id)
        return {
            "executor_registered": True,
            "source_count": len(manifests),
            "idea_count": len(open_source_ideas),
            "persisted_idea_ids": open_source_persisted_ids,
            "ideas": [idea.model_dump(mode="json") for idea in open_source_ideas],
            "output_ref": f"open_source_strategy_ideas:{len(open_source_persisted_ids)}",
        }

    def _handle_materialize_drafts(self, task: AgentTask) -> dict:
        requested_ids = set(task.input_payload.get("idea_ids", []))
        seed_ideas = [
            idea
            for idea in self.strategy_repo.list_ideas()
            if (not requested_ids or idea.idea_id in requested_ids)
            and idea.source.startswith("open_source:")
            and idea.intake_bucket == "rule_candidate"
        ]
        created_drafts: list[StrategyDraft] = []
        for idea in seed_ideas:
            created_drafts.append(self.strategy_repo.create_draft(_draft_from_open_source_idea(idea)))
        return {
            "executor_registered": True,
            "idea_count": len(seed_ideas),
            "draft_count": len(created_drafts),
            "draft_ids": [draft.draft_id for draft in created_drafts if draft.draft_id is not None],
            "output_ref": f"strategy_drafts:{len(created_drafts)}",
        }

    def _handle_deterministic_veto(self, task: AgentTask) -> dict:
        risk_events = task.input_payload.get("risk_events", [])
        high_risk_events = [
            event for event in risk_events if str(event.get("severity", "")).lower() in {"high", "critical"}
        ]
        forced_reason = task.input_payload.get("forced_veto_reason")
        veto_result = DecisionVetoResult(
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
            "completed": True,
            "executor_name": "deterministic_decision_veto",
            "veto_result": veto_result.model_dump(mode="json"),
            "risk_event_count": len(risk_events),
            "high_risk_event_count": len(high_risk_events),
            "output_ref": f"decision_veto:{task.agent_task_id}",
        }

    def _handle_summarize_failures(self, task: AgentTask) -> dict:
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

    def _handle_research_council_review(self, task: AgentTask) -> dict:
        candidate_id = str(task.input_payload.get("candidate_id") or task.agent_task_id)
        verdict = self.research_council.review(candidate_id, task.input_payload)
        return {
            "executor_registered": True,
            "completed": True,
            "executor_name": "research_council",
            "verdict": verdict.model_dump(mode="json"),
            "order_side_effects": False,
            "output_ref": f"research_council:{candidate_id}",
        }

    def _execute_llm_classification(self, task: AgentTask) -> dict:
        try:
            result = self.llm_runtime.generate_structured(
                agent_type=task.agent_type,
                task_type=task.task_type,
                payload=task.input_payload,
            )
        except Exception as exc:  # pragma: no cover - exercised by timeout path below
            return {
                "executor_registered": True,
                "completed": False,
                "executor_name": "llm_structured_classifier",
                "message": str(exc),
                "schema_validation_status": "runtime_error",
                "provider_trace": {},
                "attempt_history": [{"status": "runtime_error", "message": str(exc)}],
                "output_ref": f"agent_task:{task.agent_task_id}",
            }

        raw_output = result.get("raw_output", {})
        provider_trace = {
            "provider": result.get("provider", "unknown"),
            "model": result.get("model", "unknown"),
        }
        is_valid = all(key in raw_output for key in ("severity", "summary"))
        return {
            "executor_registered": True,
            "completed": is_valid,
            "task_status": "completed" if is_valid else "failed",
            "executor_name": "llm_structured_classifier",
            "message": None if is_valid else "llm structured output failed schema validation",
            "schema_validation_status": "passed" if is_valid else "failed",
            "provider_trace": provider_trace,
            "usage": result.get("usage") or {},
            "attempt_history": [
                {
                    "status": "passed" if is_valid else "schema_failed",
                    "provider": provider_trace["provider"],
                    "model": provider_trace["model"],
                }
            ],
            "classification": raw_output if is_valid else None,
            "output_ref": f"agent_task:{task.agent_task_id}",
        }

    def _execute_llm_market_review(self, task: AgentTask) -> dict:
        """Advisory MARKET_REVIEW: never creates candidates or blocks entry/exit."""
        return self._execute_llm_advisory_review(task, output_key="market_review", executor_name="llm_market_review")

    def _execute_llm_trade_review(self, task: AgentTask) -> dict:
        """Advisory TRADE_REVIEW: never blocks entry/exit or changes veto decisions."""
        return self._execute_llm_advisory_review(task, output_key="trade_review", executor_name="llm_trade_review")

    def _execute_llm_advisory_review(self, task: AgentTask, *, output_key: str, executor_name: str) -> dict:
        try:
            result = self.llm_runtime.generate_structured(
                agent_type=task.agent_type,
                task_type=task.task_type,
                payload=task.input_payload,
            )
            raw_output = result.get("raw_output", {})
            advisory = _validate_advisory_review_output(raw_output)
            provider_trace = {
                "provider": result.get("provider", "unknown"),
                "model": result.get("model", "unknown"),
            }
            return {
                "executor_registered": True,
                "completed": True,
                "task_status": "completed",
                "executor_name": executor_name,
                "schema_validation_status": "passed",
                "provider_trace": provider_trace,
                "usage": result.get("usage") or {},
                "attempt_history": [{"status": "passed", **provider_trace}],
                output_key: advisory,
                "output_ref": f"{output_key}:{task.agent_task_id}",
            }
        except TimeoutError as exc:
            return {
                "executor_registered": True,
                "completed": False,
                "task_status": "failed",
                "executor_name": executor_name,
                "message": str(exc),
                "schema_validation_status": "timeout",
                "provider_trace": {},
                "attempt_history": [{"status": "timeout", "message": str(exc)}],
                output_key: None,
                "output_ref": f"{output_key}:{task.agent_task_id}",
            }
        except RuntimeError as exc:
            logger.warning("llm %s runtime unavailable: %s", output_key, exc)
            return {
                "executor_registered": True,
                "completed": False,
                "task_status": "failed",
                "executor_name": executor_name,
                "message": str(exc),
                "schema_validation_status": "provider_unavailable",
                "provider_trace": {},
                "attempt_history": [{"status": "provider_unavailable", "message": str(exc)}],
                output_key: None,
                "output_ref": f"{output_key}:{task.agent_task_id}",
            }
        except Exception as exc:
            logger.warning("llm %s schema validation failed: %s", output_key, exc)
            return {
                "executor_registered": True,
                "completed": False,
                "task_status": "failed",
                "executor_name": executor_name,
                "message": str(exc),
                "schema_validation_status": "failed",
                "provider_trace": {},
                "attempt_history": [{"status": "failed", "message": str(exc)}],
                output_key: None,
                "output_ref": f"{output_key}:{task.agent_task_id}",
            }

    def _execute_llm_veto(self, task: AgentTask) -> dict:
        try:
            result = self.llm_runtime.generate_structured(
                agent_type=task.agent_type,
                task_type=task.task_type,
                payload=task.input_payload,
            )
            raw_output = result.get("raw_output", {})
            if not isinstance(raw_output.get("veto"), bool) or not isinstance(raw_output.get("veto_reason"), str):
                raise ValueError("llm veto output failed schema validation")
            provider_trace = {
                "provider": result.get("provider", "unknown"),
                "model": result.get("model", "unknown"),
            }
            return {
                "executor_registered": True,
                "completed": True,
                "task_status": "completed",
                "executor_name": "llm_decision_veto",
                "schema_validation_status": "passed",
                "provider_trace": provider_trace,
                "usage": result.get("usage") or {},
                "attempt_history": [{"status": "passed", **provider_trace}],
                "veto_result": raw_output,
                "output_ref": f"decision_veto:{task.agent_task_id}",
            }
        except TimeoutError as exc:
            return {
                "executor_registered": True,
                "completed": False,
                "task_status": "failed",
                "executor_name": "llm_decision_veto",
                "message": str(exc),
                "schema_validation_status": "timeout",
                "provider_trace": {},
                "attempt_history": [{"status": "timeout", "message": str(exc)}],
                "safe_veto_applied": True,
                "veto_result": {
                    "veto": True,
                    "veto_reason": "llm timeout -> fail closed",
                    "agent_task_ref": task.agent_task_id,
                },
                "output_ref": f"decision_veto:{task.agent_task_id}",
            }
        except RuntimeError as exc:
            # UnavailableLLMRuntime (no provider configured) and
            # LLMProviderUnavailable (all fallback candidates exhausted) both
            # surface as RuntimeError. Distinct from a genuine schema failure
            # below so operators don't mistake "no API key" for "bad LLM output".
            logger.warning("llm decision veto runtime unavailable: %s", exc)
            return {
                "executor_registered": True,
                "completed": False,
                "task_status": "failed",
                "executor_name": "llm_decision_veto",
                "message": str(exc),
                "schema_validation_status": "provider_unavailable",
                "provider_trace": {},
                "attempt_history": [{"status": "provider_unavailable", "message": str(exc)}],
                "safe_veto_applied": True,
                "veto_result": {
                    "veto": True,
                    "veto_reason": "llm runtime not configured or unavailable -> fail closed",
                    "agent_task_ref": task.agent_task_id,
                },
                "output_ref": f"decision_veto:{task.agent_task_id}",
            }
        except Exception as exc:
            logger.warning("llm decision veto schema validation failed: %s", exc)
            return {
                "executor_registered": True,
                "completed": False,
                "task_status": "failed",
                "executor_name": "llm_decision_veto",
                "message": str(exc),
                "schema_validation_status": "failed",
                "provider_trace": {},
                "attempt_history": [{"status": "failed", "message": str(exc)}],
                "safe_veto_applied": True,
                "veto_result": {
                    "veto": True,
                    "veto_reason": "schema validation failed -> fail closed",
                    "agent_task_ref": task.agent_task_id,
                },
                "output_ref": f"decision_veto:{task.agent_task_id}",
            }

    def _record_alpha_rejection_if_needed(self, idea: StrategyIdea) -> None:
        if self.review_repo is None or idea.idea_id is None or idea.intake_bucket != "subjective_to_drop":
            return
        metadata = idea.intake_metadata
        unsupported_inputs = metadata.get("unsupported_inputs", [])
        unsupported_operators = metadata.get("unsupported_operators", [])
        evaluation_error = metadata.get("evaluation_error")
        reason_parts = []
        if unsupported_inputs:
            reason_parts.append(f"unsupported_inputs={','.join(map(str, unsupported_inputs))}")
        if unsupported_operators:
            reason_parts.append(f"unsupported_operators={','.join(map(str, unsupported_operators))}")
        if evaluation_error:
            reason_parts.append(f"evaluation_error={evaluation_error}")
        reason = "; ".join(reason_parts) or "alpha expression is not executable in the supported crypto subset"
        self.review_repo.create_failure(
            FailureRecord(
                idea_id=idea.idea_id,
                origin_run_type="research_intake",
                origin_run_id=idea.idea_id,
                failure_type="alpha_evaluator_reject",
                failure_summary=f"Local alpha rejected during intake: {reason}",
                evidence_refs=[
                    f"strategy_idea:{idea.idea_id}",
                    f"raw_expression:{str(metadata.get('raw_expression', idea.source_ref or ''))[:180]}",
                ],
                recommended_change=(
                    "Keep as research-only or manually port unsupported fields/operators to crypto-native inputs."
                ),
            )
        )


def _validate_advisory_review_output(raw_output: dict) -> dict:
    bias = raw_output.get("bias")
    confidence = raw_output.get("confidence")
    summary = raw_output.get("summary")
    risk_flags = raw_output.get("risk_flags")
    if bias not in {"support", "neutral", "oppose"}:
        raise ValueError("advisory review bias failed schema validation")
    if not isinstance(confidence, (int, float)) or not 0 <= float(confidence) <= 1:
        raise ValueError("advisory review confidence failed schema validation")
    if not isinstance(summary, str):
        raise ValueError("advisory review summary failed schema validation")
    if not isinstance(risk_flags, list) or any(not isinstance(item, str) for item in risk_flags):
        raise ValueError("advisory review risk_flags failed schema validation")
    return {
        "bias": bias,
        "confidence": float(confidence),
        "risk_flags": risk_flags,
        "summary": summary,
        "advisory_only": True,
    }


def _draft_from_open_source_idea(idea: StrategyIdea) -> StrategyDraft:
    title = idea.title.lower()
    if "funding" in title or "carry" in title:
        rules = StrategyRules(
            entry_rules={"funding_threshold_bps": 5, "basis_filter_bps": 20, "requires_hedged_spot_perp": True},
            exit_rules={"hold_hours": 8, "exit_on_funding_flip": True},
            stoploss_rules={"basis_bps": 40, "max_net_loss_bps": 30},
            takeprofit_rules={"close_after_windows": 1, "min_net_profit_bps": 8},
            position_rules={"notional_usdt": 1000, "max_leverage": 1, "paper_only": False},
        )
        market_regime = "funding_extreme"
        risk_level = RiskLevel.LOW
    elif "grid" in title or "market making" in title:
        rules = StrategyRules(
            entry_rules={"grid_spacing_bps": 25, "reference_price": "latest_mid", "paper_only": True},
            exit_rules={"rebalance_on_inventory_skew": True, "max_runtime_hours": 24},
            stoploss_rules={"stop_on_volatility_bps": 250, "stop_on_spread_bps": 80},
            takeprofit_rules={"per_grid_takeprofit_bps": 20},
            position_rules={"max_inventory_usdt": 500, "order_notional_usdt": 50, "paper_only": True},
        )
        market_regime = "range_bound"
        risk_level = RiskLevel.HIGH
    else:
        rules = StrategyRules(
            entry_rules={"ema_fast": 20, "ema_slow": 50, "macd_confirmation": True, "adx_min": 20},
            exit_rules={"exit_on_macd_cross_down": True, "max_hold_bars": 48},
            stoploss_rules={"atr_multiple": 2.0, "structure_stop_required": True},
            takeprofit_rules={"risk_reward": 2.0, "trail_after_r": 1.0},
            position_rules={"risk_per_trade": 0.01, "max_leverage": 2},
        )
        market_regime = "trend"
        risk_level = RiskLevel.MEDIUM
    return StrategyDraft(
        idea_id=idea.idea_id,
        title=idea.title,
        source=idea.source,
        core_thesis=idea.hypothesis_summary,
        market=idea.market,
        symbol_scope=idea.symbol_scope,
        timeframe=Timeframe.H1,
        market_regime=market_regime,
        risk_level=risk_level,
        rules=rules,
        draft_status="drafting",
        review_notes=[
            "seeded from open-source research manifest",
            "external code not imported; rules must pass validation before paper/live",
        ],
    )
