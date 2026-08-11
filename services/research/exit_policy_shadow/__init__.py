"""P2-A exit-policy shadow evaluation (read-only). See ADR-004."""

from services.research.exit_policy_shadow.contracts import (
    ExitPolicyId,
    RealEntry,
    ShadowOutcome,
    Verdict,
)
from services.research.exit_policy_shadow.replay import replay_entry_under_policy

__all__ = [
    "ExitPolicyId",
    "RealEntry",
    "ShadowOutcome",
    "Verdict",
    "replay_entry_under_policy",
]
