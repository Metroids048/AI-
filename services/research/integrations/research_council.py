"""Evidence-grounded research council; it can review, never trade."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal, cast

from pydantic import Field

from shared.models import PlatformModel


class ResearchCouncilVerdict(PlatformModel):
    candidate_id: str
    verdict: Literal["accept_for_next_gate", "reject", "insufficient_evidence"]
    roles: dict[str, dict[str, Any]] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
    provider: str = "deterministic"
    model: str = "native"
    total_tokens: int = 0
    output_hash: str
    order_side_effects: bool = False


class ResearchCouncil:
    roles = ("technical_researcher", "bull_researcher", "bear_researcher", "risk_manager", "portfolio_manager")

    def __init__(self, llm_runtime: Any | None = None) -> None:
        self.llm_runtime = llm_runtime

    def review(self, candidate_id: str, evidence: dict[str, Any]) -> ResearchCouncilVerdict:
        evidence_refs = [str(ref) for ref in evidence.get("evidence_refs", [])]
        roles: dict[str, dict[str, Any]] = {
            role: {"status": "reviewed", "evidence_refs": evidence_refs} for role in self.roles
        }
        if not evidence_refs:
            verdict = "insufficient_evidence"
        elif evidence.get("bias_status") == "FAIL" or evidence.get("native_oos_status") == "FAIL":
            verdict = "reject"
        else:
            verdict = "accept_for_next_gate"
        payload = {"candidate_id": candidate_id, "verdict": verdict, "roles": roles, "evidence_refs": evidence_refs}
        output_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return ResearchCouncilVerdict(
            candidate_id=candidate_id,
            verdict=cast(Literal["accept_for_next_gate", "reject", "insufficient_evidence"], verdict),
            roles=roles,
            evidence_refs=evidence_refs,
            output_hash=output_hash,
        )
