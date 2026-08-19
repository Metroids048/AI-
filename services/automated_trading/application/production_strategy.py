"""Fail-closed production strategy authorization and V2 candidate adapter.

This module deliberately has no execution repository, exchange adapter, or
writer dependency.  It turns an explicitly approved immutable runtime snapshot
into a V2 ``TradeCandidate``; execution remains owned by the existing V2 cycle.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any

from services.automated_trading.application.canonical_strategy_manifest import (
    ManifestValidationError,
    load_canonical_strategy_manifest,
)
from services.automated_trading.domain.candidates import CandidateLane, TradeCandidate
from services.automated_trading.domain.enums import V2CandidateType
from services.execution.bootstrap import AUTO_PAPER_EXECUTION_SYMBOLS, AUTO_PAPER_TECHNICAL_KEY, CANONICAL_MANIFEST_ROOT
from services.execution.decision_pipeline import DecisionPipeline, DecisionPipelineResult
from services.execution.signal_edge_stats import strategy_rules_hash
from services.strategy_library.candidates.registry import get_candidate
from services.strategy_library.context import MarketContextBuilder
from services.strategy_library.proposal_pipeline import run_proposal_pipeline
from services.strategy_library.v2_projection import project_single_target
from shared.models import StrategyContract, StrategyRules

NO_AUTHORIZED_PRODUCTION_STRATEGY = "NO_AUTHORIZED_PRODUCTION_STRATEGY"


class EntryAuthority(StrEnum):
    """The only writer allowed to create new exposure in one V2 cycle."""

    PRODUCTION = "PRODUCTION"
    TESTNET_CANARY = "TESTNET_CANARY"
    NONE = "NONE"


@dataclass(frozen=True)
class EntryAuthorityResolution:
    authority: EntryAuthority
    reason: str
    active_strategy_id: str | None
    promotion_eligible: bool


def resolve_entry_authority(
    *,
    production_authorized: bool,
    production_strategy_id: str | None,
    execution_mode: str,
    operator_testnet_canary_enabled: bool,
) -> EntryAuthorityResolution:
    """Resolve exactly one new-exposure writer without changing authorization.

    A pending production authorization never becomes approved here.  The
    explicitly operator-enabled Canary is a Testnet-only continuity lane and
    stays permanently non-promotable.
    """
    if production_authorized:
        return EntryAuthorityResolution(
            EntryAuthority.PRODUCTION,
            "production_approved",
            production_strategy_id,
            True,
        )
    if execution_mode == "BINANCE_TESTNET" and operator_testnet_canary_enabled:
        return EntryAuthorityResolution(
            EntryAuthority.TESTNET_CANARY,
            "testnet_canary_enabled",
            "testnet_sampling_v2",
            False,
        )
    reason = "operator_disabled" if execution_mode == "BINANCE_TESTNET" else "production_pending"
    return EntryAuthorityResolution(EntryAuthority.NONE, reason, None, False)


@dataclass(frozen=True)
class ProductionAuthorization:
    """The manifest authorization outcome for one active ConfigSnapshot."""

    authorized: bool
    reason: str
    candidate_id: str | None = None
    candidate_version: str | None = None
    rules: StrategyRules | None = None
    validation_evidence_ref: str | None = None
    approval_identity: str | None = None
    approval_time: str | None = None


@dataclass(frozen=True)
class ProductionDecision:
    """Pure production decision and enough provenance for the V2 funnel."""

    authorization: ProductionAuthorization
    candidate: TradeCandidate | None
    reason: str
    trace: dict[str, Any]


def _manifest_path() -> Path:
    return CANONICAL_MANIFEST_ROOT / f"{AUTO_PAPER_TECHNICAL_KEY}.json"


def resolve_production_authorization(
    *,
    snapshot_config: dict[str, Any] | None,
    snapshot_hash: str | None,
    symbol: str,
) -> ProductionAuthorization:
    """Validate authorization against the active immutable snapshot.

    The manifest can nominate a candidate, but only an ``APPROVED`` record
    bound to the same rules and snapshot can grant entry authority.
    """
    try:
        manifest = load_canonical_strategy_manifest(_manifest_path())
        if not manifest.is_approved:
            return ProductionAuthorization(False, NO_AUTHORIZED_PRODUCTION_STRATEGY)
        candidate_id = manifest.strategy_id
        candidate = get_candidate(candidate_id)
        if candidate_id == "aggressive_multi_regime_v1" and (
            candidate.lifecycle_state != "APPROVED" or not candidate.execution_eligible
        ):
            return ProductionAuthorization(False, NO_AUTHORIZED_PRODUCTION_STRATEGY)
        if manifest.strategy_version != candidate.version:
            return ProductionAuthorization(False, NO_AUTHORIZED_PRODUCTION_STRATEGY)
        approved_symbols = set(manifest.eligible_execution_symbols)
        if approved_symbols != set(AUTO_PAPER_EXECUTION_SYMBOLS) or symbol not in approved_symbols:
            return ProductionAuthorization(False, NO_AUTHORIZED_PRODUCTION_STRATEGY)
        evidence_ref = manifest.validation_evidence.get("report_ref")
        approval = manifest.approval
        if not isinstance(evidence_ref, str) or not evidence_ref.strip():
            return ProductionAuthorization(False, NO_AUTHORIZED_PRODUCTION_STRATEGY)
        approved_by = approval.get("approved_by")
        approved_at = approval.get("approved_at")
        if not isinstance(approved_by, str) or not approved_by or not isinstance(approved_at, str) or not approved_at:
            return ProductionAuthorization(False, NO_AUTHORIZED_PRODUCTION_STRATEGY)
        if not snapshot_hash or manifest.config_snapshot_hash != snapshot_hash:
            return ProductionAuthorization(False, NO_AUTHORIZED_PRODUCTION_STRATEGY)
        if not isinstance(snapshot_config, dict):
            return ProductionAuthorization(False, NO_AUTHORIZED_PRODUCTION_STRATEGY)
        raw_rules = snapshot_config.get("strategy_rules")
        if not isinstance(raw_rules, dict):
            return ProductionAuthorization(False, NO_AUTHORIZED_PRODUCTION_STRATEGY)
        rules = StrategyRules(**raw_rules)
        rules_hash = strategy_rules_hash(rules)
        if rules_hash != manifest.rules_hash:
            return ProductionAuthorization(False, NO_AUTHORIZED_PRODUCTION_STRATEGY)
        if str(rules.entry_rules.get("candidate_id")) != candidate_id:
            return ProductionAuthorization(False, NO_AUTHORIZED_PRODUCTION_STRATEGY)
        return ProductionAuthorization(
            True,
            "AUTHORIZED",
            candidate_id=candidate_id,
            candidate_version=candidate.version,
            rules=rules,
            validation_evidence_ref=evidence_ref,
            approval_identity=approved_by,
            approval_time=approved_at,
        )
    except (KeyError, OSError, TypeError, ValueError, ManifestValidationError):
        return ProductionAuthorization(False, NO_AUTHORIZED_PRODUCTION_STRATEGY)


def _strategy_contract(authorization: ProductionAuthorization) -> StrategyContract:
    assert authorization.rules is not None
    assert authorization.candidate_id is not None
    return StrategyContract(
        strategy_id=authorization.candidate_id,
        strategy_key=AUTO_PAPER_TECHNICAL_KEY,
        source="approved_active_manifest",
        core_thesis="Explicitly approved production candidate evaluated through the mature 4h/1h/15m pipeline.",
        symbol_scope=list(AUTO_PAPER_EXECUTION_SYMBOLS),
        rules=authorization.rules,
    )


def evaluate_authorized_production_strategy(
    *,
    authorization: ProductionAuthorization,
    data_repo: Any,
    cycle_id: str,
    symbol: str,
    now: datetime,
    candidate_ttl_seconds: int = 75,
) -> ProductionDecision:
    """Run the legacy mature decision logic as a read-only V2 strategy adapter."""
    if not authorization.authorized:
        return ProductionDecision(authorization, None, authorization.reason, {})
    assert authorization.rules is not None
    assert authorization.candidate_id is not None
    assert authorization.candidate_version is not None
    if authorization.candidate_id == "aggressive_multi_regime_v1":
        bars_by_timeframe = {
            timeframe: data_repo.list_ohlcv_bars(symbol=symbol, timeframe=timeframe, limit=80)
            for timeframe in ("1m", "5m", "15m", "1h", "4h")
        }
        context = MarketContextBuilder().build(symbol=symbol, decision_time=now, bars_by_timeframe=bars_by_timeframe)
        result = run_proposal_pipeline(context)
        proposal = result.selection.selected
        trace: dict[str, Any] = {
            "production_authorization": "APPROVED",
            "validation_evidence_ref": authorization.validation_evidence_ref or "",
            "approval_identity": authorization.approval_identity or "",
            "approval_time": authorization.approval_time or "",
            "candidate_id": authorization.candidate_id,
            "candidate_version": authorization.candidate_version,
            "rules_hash": strategy_rules_hash(authorization.rules),
            "proposal_pipeline": result.model_dump(mode="json"),
        }
        if proposal is None:
            return ProductionDecision(authorization, None, result.selection.status, trace)
        geometry = project_single_target(proposal)
        candidate = TradeCandidate(
            candidate_id=str(uuid.uuid4()),
            cycle_id=cycle_id,
            strategy_id=authorization.candidate_id,
            strategy_version=authorization.candidate_version,
            lane=CandidateLane.PRODUCTION,
            candidate_type=V2CandidateType.PRIMARY,
            symbol=symbol,
            side="LONG" if proposal.side == "long" else "SHORT",
            signal_candle_close_time=proposal.signal_bar_time,
            signal_reference_price=geometry.entry_reference_price,
            confidence=Decimal(str(result.selection.selected_score or 0)),
            stop_distance=geometry.stop_distance,
            take_profit_distance=geometry.take_profit_distance,
            max_entry_drift_bps=proposal.entry_trigger.max_price_drift_bps,
            expires_at=min(proposal.expires_at, now + timedelta(seconds=candidate_ttl_seconds)),
            non_promotable=False,
            signal_context=tuple(
                sorted(
                    {
                        "target_label": geometry.target_label,
                        "validation_evidence_ref": authorization.validation_evidence_ref or "",
                        "approval_identity": authorization.approval_identity or "",
                        "approval_time": authorization.approval_time or "",
                        "rules_hash": strategy_rules_hash(authorization.rules),
                    }.items()
                )
            ),
        )
        return ProductionDecision(authorization, candidate, "CANDIDATE_READY", trace)

    decision: DecisionPipelineResult = DecisionPipeline(data_repo=data_repo).evaluate(
        strategy=_strategy_contract(authorization),
        symbol=symbol,
        timeframe=str(authorization.rules.entry_rules.get("entry_timeframe", "15m")),
        enable_decision_veto=False,
        decision_time=now,
    )
    trace = dict(decision.trace)
    trace.update(
        {
            "production_authorization": "APPROVED",
            "validation_evidence_ref": authorization.validation_evidence_ref or "",
            "approval_identity": authorization.approval_identity or "",
            "approval_time": authorization.approval_time or "",
        }
    )
    if not decision.should_trade or decision.direction is None or decision.bar_time is None or decision.atr is None:
        return ProductionDecision(authorization, None, decision.reason, trace)
    atr_multiple = Decimal(str(authorization.rules.stoploss_rules.get("atr_multiple", "0")))
    reward = Decimal(str(authorization.rules.takeprofit_rules.get("risk_reward", "0")))
    stop_distance = Decimal(str(decision.atr)) * atr_multiple
    if stop_distance <= 0 or reward <= 0:
        return ProductionDecision(authorization, None, "strategy_geometry_unavailable", trace)
    candidate = TradeCandidate(
        candidate_id=str(uuid.uuid4()),
        cycle_id=cycle_id,
        strategy_id=authorization.candidate_id,
        strategy_version=authorization.candidate_version,
        lane=CandidateLane.PRODUCTION,
        candidate_type=V2CandidateType.PRIMARY,
        symbol=symbol,
        side="LONG" if decision.direction.value == "long" else "SHORT",
        signal_candle_close_time=decision.bar_time,
        signal_reference_price=decision.reference_price,
        confidence=Decimal(str(decision.confidence_multiplier)),
        stop_distance=stop_distance,
        take_profit_distance=stop_distance * reward,
        max_entry_drift_bps=Decimal("20"),
        expires_at=now + timedelta(seconds=candidate_ttl_seconds),
        non_promotable=False,
        signal_context=tuple(sorted((str(key), str(value)) for key, value in trace.items())),
    )
    return ProductionDecision(authorization, candidate, "CANDIDATE_READY", trace)
