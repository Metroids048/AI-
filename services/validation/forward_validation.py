"""Immutable handoff contract from validation into Testnet forward mode.

The handoff is a validation artifact, not a Production approval.  It carries
the exact strategy/package/evidence identity that the V2 runtime must consume.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any

from pydantic import Field, model_validator

from services.automated_trading.application.strategy_package_identity import strategy_package_identity
from services.execution.signal_edge_stats import strategy_rules_hash
from services.strategy_library.canonical import canonical_hash
from services.validation.strategy_promotion import PromotionResult
from shared.models import StrategyRules
from shared.models.trading import ImmutableContract, canonical_config_hash


class ForwardDensityMetrics(ImmutableContract):
    """Closed-bar density evidence used to decide whether forward collection is viable."""

    eligible_closed_bars: int = Field(ge=0)
    candidate_count: int = Field(ge=0)
    closed_trade_count: int = Field(ge=0)
    closed_trades_per_day: float = Field(ge=0)
    median_inter_trade_hours: float = Field(ge=0)
    p90_inter_trade_hours: float = Field(ge=0)
    estimated_days_to_30_closed_trades: float = Field(ge=0)
    target_forward_closed_trades: int = Field(default=30, ge=1)
    max_estimated_days: float = Field(default=60, gt=0)
    passed: bool

    @model_validator(mode="after")
    def validate_frozen_thresholds(self) -> ForwardDensityMetrics:
        if self.target_forward_closed_trades != 30:
            raise ValueError("forward target is frozen at 30 closed trades")
        if self.max_estimated_days != 60:
            raise ValueError("forward density horizon is frozen at 60 days")
        expected = self.estimated_days_to_30_closed_trades <= self.max_estimated_days
        if self.passed != expected:
            raise ValueError("forward density pass does not match estimated collection horizon")
        return self


class ForwardValidationHandoff(ImmutableContract):
    """A deterministic, immutable, non-Production validation handoff."""

    schema_version: str = "forward-validation-handoff-v2"
    status: str = "FORWARD_CANDIDATE_READY"
    candidate_id: str
    candidate_version: str
    strategy_id: str
    strategy_version: str
    rules_hash: str
    strategy_code_hash: str
    strategy_package_hash: str
    dataset_hash: str
    validation_evidence_ref: str
    validation_evidence_hash: str
    eligible_execution_symbols: tuple[str, ...]
    strategy_rules: dict[str, Any]
    runtime_config_binding_hash: str
    package_identity: dict[str, str]
    density: ForwardDensityMetrics
    profitability_recovery_passed: bool
    forward_density_passed: bool
    frozen_at: datetime
    handoff_hash: str

    @model_validator(mode="after")
    def validate_integrity(self) -> ForwardValidationHandoff:
        if self.schema_version != "forward-validation-handoff-v2":
            raise ValueError("forward-validation-handoff-v1 is rejected due to circular snapshot binding")
        if self.status != "FORWARD_CANDIDATE_READY":
            raise ValueError("forward handoff status must be FORWARD_CANDIDATE_READY")
        if self.candidate_id != self.strategy_id or self.candidate_version != self.strategy_version:
            raise ValueError("candidate and strategy identities must be exact")
        if len(self.rules_hash) != 64 or len(self.strategy_code_hash) != 64 or len(self.strategy_package_hash) != 64:
            raise ValueError("strategy identity hashes must be SHA-256")
        if len(self.dataset_hash) != 64 or len(self.validation_evidence_hash) != 64:
            raise ValueError("dataset and validation evidence hashes must be SHA-256")
        if not self.eligible_execution_symbols or len(set(self.eligible_execution_symbols)) != len(
            self.eligible_execution_symbols
        ):
            raise ValueError("eligible_execution_symbols must be a non-empty unique tuple")
        if not self.profitability_recovery_passed or not self.forward_density_passed or not self.density.passed:
            raise ValueError("only passing profitability and density gates may create a forward handoff")
        expected_rules_hash = strategy_rules_hash(StrategyRules(**self.strategy_rules))
        if expected_rules_hash != self.rules_hash:
            raise ValueError("forward handoff rules hash mismatch")
        expected_identity = {
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "rules_hash": self.rules_hash,
            "strategy_code_hash": self.strategy_code_hash,
            "strategy_package_hash": self.strategy_package_hash,
        }
        if self.package_identity != expected_identity:
            raise ValueError("forward handoff package identity mismatch")
        content = self.model_dump(mode="json")
        content.pop("handoff_hash", None)
        if self.handoff_hash != canonical_hash(content):
            raise ValueError("FORWARD_VALIDATION_HANDOFF_HASH_MISMATCH")
        return self


def forward_runtime_binding_hash(snapshot_config: dict[str, Any]) -> str:
    """Hash the runtime config without the self-referential handoff field."""
    if not isinstance(snapshot_config, dict):
        raise ValueError("snapshot_config must be a dictionary")
    binding_config = deepcopy(snapshot_config)
    binding_config.pop("forward_validation", None)
    return canonical_config_hash(binding_config)


def build_forward_validation_handoff(
    *,
    candidate_id: str,
    candidate_version: str,
    strategy_rules: dict[str, Any],
    dataset_hash: str,
    validation_evidence_ref: str,
    validation_evidence_hash: str,
    eligible_execution_symbols: tuple[str, ...],
    density: ForwardDensityMetrics,
    profitability_recovery: PromotionResult,
    runtime_config: dict[str, Any],
    frozen_at: datetime,
) -> ForwardValidationHandoff:
    """Create the only supported validation-to-forward handoff.

    The handoff binds to the strategy-relevant runtime config with its own
    ``forward_validation`` field removed. This avoids a cryptographic
    self-reference while allowing the active ConfigSnapshot to be validated
    independently by the runtime.
    """
    if not profitability_recovery.eligible:
        raise ValueError("PROFITABILITY_RECOVERY_NOT_PASS")
    if not density.passed:
        raise ValueError("FORWARD_DENSITY_NOT_PASS")
    if not dataset_hash or len(dataset_hash) != 64:
        raise ValueError("dataset_hash must be SHA-256")
    if not validation_evidence_ref.strip() or len(validation_evidence_hash) != 64:
        raise ValueError("validation evidence reference and SHA-256 are required")
    rules = StrategyRules(**strategy_rules)
    rules_hash = strategy_rules_hash(rules)
    identity = strategy_package_identity(
        strategy_id=candidate_id,
        strategy_version=candidate_version,
        rules_hash=rules_hash,
    )
    runtime_binding_hash = forward_runtime_binding_hash(runtime_config)
    payload: dict[str, Any] = {
        "schema_version": "forward-validation-handoff-v2",
        "status": "FORWARD_CANDIDATE_READY",
        "candidate_id": candidate_id,
        "candidate_version": candidate_version,
        "strategy_id": candidate_id,
        "strategy_version": candidate_version,
        "rules_hash": rules_hash,
        "strategy_code_hash": identity.strategy_code_hash,
        "strategy_package_hash": identity.strategy_package_hash,
        "dataset_hash": dataset_hash,
        "validation_evidence_ref": validation_evidence_ref,
        "validation_evidence_hash": validation_evidence_hash,
        "eligible_execution_symbols": eligible_execution_symbols,
        "strategy_rules": strategy_rules,
        "runtime_config_binding_hash": runtime_binding_hash,
        "package_identity": identity.snapshot_binding(),
        "density": density,
        "profitability_recovery_passed": True,
        "forward_density_passed": True,
        "frozen_at": frozen_at,
    }
    normalized = ForwardValidationHandoff.model_construct(**payload, handoff_hash="")
    content = normalized.model_dump(mode="json")
    content.pop("handoff_hash", None)
    payload["handoff_hash"] = canonical_hash(content)
    return ForwardValidationHandoff.model_validate(payload)
