"""Parser for the single active strategy manifest.

The JSON document remains the authoritative strategy record.  This module is
only a strict, side-effect-free reader shared by bootstrap, runtime and audit
code so none of those consumers can silently invent a different scope.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ManifestValidationError(ValueError):
    """The packaged active-strategy manifest cannot safely be consumed."""


_AUTHORIZATION_STATES = frozenset({"NOT_READY", "PENDING", "APPROVED", "REVOKED"})


def _symbols(raw: object, *, field: str) -> tuple[str, ...]:
    if not isinstance(raw, list) or any(not isinstance(item, str) or not item for item in raw):
        raise ManifestValidationError(f"{field} must be a non-empty symbol list")
    symbols = tuple(raw)
    if len(set(symbols)) != len(symbols):
        raise ManifestValidationError(f"{field} contains duplicate symbols")
    return symbols


@dataclass(frozen=True)
class CanonicalStrategyManifest:
    strategy_key: str
    strategy_id: str
    strategy_version: str
    rules_hash: str
    commit_sha: str
    configured_execution_scope: tuple[str, ...]
    eligible_execution_symbols: tuple[str, ...]
    research_symbols: tuple[str, ...]
    validation_evidence: dict[str, Any]
    golden_behavior_ref: str | None
    authorization_state: str
    approval: dict[str, Any]
    config_snapshot_hash: str | None
    effective_at: str

    @property
    def is_approved(self) -> bool:
        return self.authorization_state == "APPROVED"


def load_canonical_strategy_manifest(path: Path) -> CanonicalStrategyManifest:
    """Load manifest v4 and reject scope or authorization ambiguity."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestValidationError(f"unable to read manifest: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("schema_version") != 4:
        raise ManifestValidationError("active manifest must use schema_version 4")

    required_strings = (
        "strategy_key",
        "strategy_id",
        "strategy_version",
        "rules_hash",
        "commit_sha",
        "effective_at",
    )
    for field in required_strings:
        if not isinstance(raw.get(field), str) or not raw[field]:
            raise ManifestValidationError(f"{field} is required")
    if len(str(raw["rules_hash"])) != 64:
        raise ManifestValidationError("rules_hash must be SHA-256")
    if len(str(raw["commit_sha"])) != 40:
        raise ManifestValidationError("commit_sha must be a full git SHA")

    configured = _symbols(raw.get("configured_execution_scope"), field="configured_execution_scope")
    eligible = (
        _symbols(raw.get("eligible_execution_symbols"), field="eligible_execution_symbols")
        if raw.get("eligible_execution_symbols")
        else ()
    )
    if not set(eligible).issubset(configured):
        raise ManifestValidationError("eligible_execution_symbols must be within configured execution scope")
    research = _symbols(raw.get("research_symbols"), field="research_symbols")
    state = raw.get("authorization_state")
    if state not in _AUTHORIZATION_STATES:
        raise ManifestValidationError("authorization_state is invalid")
    evidence = raw.get("validation_evidence")
    approval = raw.get("approval")
    if not isinstance(evidence, dict) or not isinstance(approval, dict):
        raise ManifestValidationError("validation_evidence and approval must be objects")
    if state == "APPROVED":
        if not eligible:
            raise ManifestValidationError("APPROVED manifest must have eligible execution symbols")
        if not isinstance(approval.get("approved_by"), str) or not isinstance(approval.get("approved_at"), str):
            raise ManifestValidationError("APPROVED manifest requires approval identity and time")
        if not isinstance(raw.get("config_snapshot_hash"), str) or not raw["config_snapshot_hash"]:
            raise ManifestValidationError("APPROVED manifest requires config_snapshot_hash")

    golden = raw.get("golden_behavior_ref")
    if golden is not None and not isinstance(golden, str):
        raise ManifestValidationError("golden_behavior_ref must be a string or null")
    snapshot_hash = raw.get("config_snapshot_hash")
    if snapshot_hash is not None and not isinstance(snapshot_hash, str):
        raise ManifestValidationError("config_snapshot_hash must be a string or null")
    return CanonicalStrategyManifest(
        strategy_key=str(raw["strategy_key"]),
        strategy_id=str(raw["strategy_id"]),
        strategy_version=str(raw["strategy_version"]),
        rules_hash=str(raw["rules_hash"]),
        commit_sha=str(raw["commit_sha"]),
        configured_execution_scope=configured,
        eligible_execution_symbols=eligible,
        research_symbols=research,
        validation_evidence=dict(evidence),
        golden_behavior_ref=golden,
        authorization_state=str(state),
        approval=dict(approval),
        config_snapshot_hash=snapshot_hash,
        effective_at=str(raw["effective_at"]),
    )
