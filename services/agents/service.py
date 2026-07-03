"""Agent-task orchestration with structured I/O only."""

from __future__ import annotations

import uuid
from pathlib import Path

from apps.api.config import settings
from research_source.open_source_strategy_library import OpenSourceStrategyExtractor, OpenSourceStrategyLibrary
from research_source.worldquant_adapter import LocalAlphaScanner
from services.strategy_library import AgentTaskRepository, StrategyRepository
from shared.models import (
    AgentTask,
    AgentTaskRequest,
    DecisionVetoResult,
    RiskLevel,
    StrategyDraft,
    StrategyIdea,
    StrategyRules,
    Timeframe,
)

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
        self.open_source_library = OpenSourceStrategyLibrary()
        self.open_source_extractor = OpenSourceStrategyExtractor()

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

        if task.agent_type == "research_agent" and task.task_type == "import_open_source_sources":
            import_result = self.open_source_library.import_sources(
                source_ids=list(task.input_payload.get("source_ids", [])),
                refresh_assets=bool(task.input_payload.get("refresh_assets", True)),
                fetch_remote=bool(task.input_payload.get("fetch_remote", False)),
            )
            return {
                "executor_registered": True,
                "imported_count": len(import_result.imported),
                "failed_count": len(import_result.failed),
                "imported_source_ids": [item.source_id for item in import_result.imported],
                "failed_source_ids": [item.source_id for item in import_result.failed],
                "output_ref": f"open_source_sources:{len(import_result.imported)}",
            }

        if task.agent_type == "research_agent" and task.task_type == "extract_open_source_strategy_ideas":
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

        if task.agent_type == "strategy_agent" and task.task_type == "materialize_seed_strategy_drafts":
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

        if task.agent_type == "decision_veto_agent" and task.task_type == "pre_execution_veto":
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
                "veto_result": veto_result.model_dump(mode="json"),
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
