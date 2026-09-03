"""Machine-readable business acceptance state, separate from code freeze."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

ACCEPTANCE_STAGES = (
    "runtime_readiness",
    "natural_testnet_execution",
    "strategy_business_recovery",
    "profitability_validation",
)
VALID_STATES = frozenset({"NOT_VERIFIED", "PASS", "BLOCKED", "PENDING"})


@dataclass(frozen=True, slots=True)
class BusinessAcceptanceState:
    """Independent acceptance ledger entry tied to one immutable code SHA."""

    validated_code_sha: str | None
    runtime_readiness: str = "NOT_VERIFIED"
    natural_testnet_execution: str = "NOT_VERIFIED"
    strategy_business_recovery: str = "NOT_VERIFIED"
    profitability_validation: str = "NOT_VERIFIED"
    validated_at: str | None = None
    evidence_refs: tuple[str, ...] = field(default_factory=tuple)
    notes: str | None = None

    def __post_init__(self) -> None:
        for stage in ACCEPTANCE_STAGES:
            value = getattr(self, stage)
            if value not in VALID_STATES:
                raise ValueError(f"invalid business acceptance state for {stage}: {value}")
        if any(getattr(self, stage) == "PASS" for stage in ACCEPTANCE_STAGES) and not self.validated_code_sha:
            raise ValueError("validated_code_sha is required for business acceptance PASS")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "business-acceptance-state-1",
            "validated_code_sha": self.validated_code_sha,
            "validated_at": self.validated_at,
            **{stage: getattr(self, stage) for stage in ACCEPTANCE_STAGES},
            "evidence_refs": list(self.evidence_refs),
            "notes": self.notes,
        }

    @classmethod
    def runtime_pass(cls, *, validated_code_sha: str, evidence_refs: tuple[str, ...] = ()) -> BusinessAcceptanceState:
        return cls(
            validated_code_sha=validated_code_sha,
            runtime_readiness="PASS",
            natural_testnet_execution="BLOCKED",
            strategy_business_recovery="NOT_VERIFIED",
            profitability_validation="NOT_VERIFIED",
            validated_at=datetime.now(UTC).isoformat(),
            evidence_refs=evidence_refs,
            notes="Runtime readiness does not assert a natural signal or profitability.",
        )
