"""Deterministic identity for the code and rules that create V2 candidates.

This deliberately fingerprints the canonical strategy package, not the whole
repository.  A README, UI, or reconciliation change cannot revoke a valid
strategy approval; a candidate, signal, decision-pipeline, or geometry change
does.  Git revisions remain audit provenance in the manifest and are never an
authorization equality predicate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

_SOURCE_ROOT = Path(__file__).resolve().parents[3]

# This is an intentional, reviewed dependency inventory of the production
# candidate path.  It excludes execution/reconciliation, presentation, tests,
# the manifest itself, and unrelated repository files.  Every listed file can
# affect signal selection, direction, candidate construction, or geometry.
_COMMON_STRATEGY_SOURCES: tuple[str, ...] = (
    "services/automated_trading/application/production_strategy.py",
    "services/automated_trading/domain/candidates.py",
    "services/execution/decision_pipeline.py",
    "services/execution/net_edge.py",
    "services/execution/signal_edge_stats.py",
    "services/strategy_library/candidates/registry.py",
    "services/strategy_library/ensemble/__init__.py",
    "services/strategy_library/ensemble/service.py",
    "services/strategy_library/ensemble/weighted.py",
    "services/strategy_library/meta_label_model.py",
    "services/strategy_library/regime/__init__.py",
    "services/strategy_library/regime/router.py",
    "services/strategy_library/regime/scorer_v2.py",
    "services/strategy_library/technical/__init__.py",
    "services/strategy_library/technical/dow_trend.py",
    "services/strategy_library/technical/indicators.py",
    "services/strategy_library/technical/macd.py",
    "services/strategy_library/technical/price_action.py",
    "services/strategy_library/technical/volatility_regime.py",
)

_AGGRESSIVE_MULTI_REGIME_SOURCES: tuple[str, ...] = (
    "services/strategy_library/context.py",
    "services/strategy_library/proposal_pipeline.py",
    "services/strategy_library/proposals.py",
    "services/strategy_library/v2_projection.py",
)


@dataclass(frozen=True)
class StrategyPackageIdentity:
    """The immutable, recomputable authorization identity of one strategy."""

    strategy_id: str
    strategy_version: str
    rules_hash: str
    strategy_code_hash: str
    strategy_package_hash: str

    def snapshot_binding(self) -> dict[str, str]:
        """Return the exact identity stored in the immutable ConfigSnapshot."""
        return {
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "rules_hash": self.rules_hash,
            "strategy_code_hash": self.strategy_code_hash,
            "strategy_package_hash": self.strategy_package_hash,
        }


def strategy_source_files(strategy_id: str) -> tuple[str, ...]:
    """Return the ordered source inventory for ``strategy_id``.

    All mature technical candidates share the DecisionPipeline route.  The
    aggressive proposal candidate has a distinct, explicit addendum.  Keeping
    this mapping small and declarative makes changes reviewable and prevents a
    broad repository hash from turning harmless code changes into revocations.
    """
    sources = _COMMON_STRATEGY_SOURCES
    if strategy_id == "aggressive_multi_regime_v1":
        sources = (*sources, *_AGGRESSIVE_MULTI_REGIME_SOURCES)
    return tuple(sorted(sources))


def _strategy_code_hash(*, strategy_id: str, source_root: Path) -> str:
    digest = hashlib.sha256()
    digest.update(b"canonical-strategy-code-v1\n")
    for relative_path in strategy_source_files(strategy_id):
        path = source_root / relative_path
        try:
            # Hash the logical source text so Git checkout line-ending policy
            # cannot revoke an otherwise identical strategy package.
            with path.open("r", encoding="utf-8", newline="") as source:
                content = source.read().replace("\r\n", "\n")
            content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        except OSError as exc:
            raise ValueError(f"strategy source is unavailable: {relative_path}") from exc
        digest.update(relative_path.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(content_hash.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _strategy_package_hash(*, strategy_id: str, strategy_version: str, rules_hash: str, strategy_code_hash: str) -> str:
    payload = {
        "rules_hash": rules_hash,
        "strategy_code_hash": strategy_code_hash,
        "strategy_id": strategy_id,
        "strategy_version": strategy_version,
    }
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def strategy_package_identity(
    *,
    strategy_id: str,
    strategy_version: str,
    rules_hash: str,
    source_root: Path | None = None,
) -> StrategyPackageIdentity:
    """Compute a package identity without consulting Git or environment state."""
    if not strategy_id or not strategy_version:
        raise ValueError("strategy_id and strategy_version are required")
    if len(rules_hash) != 64:
        raise ValueError("rules_hash must be SHA-256")
    code_hash = _strategy_code_hash(strategy_id=strategy_id, source_root=source_root or _SOURCE_ROOT)
    return StrategyPackageIdentity(
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        rules_hash=rules_hash,
        strategy_code_hash=code_hash,
        strategy_package_hash=_strategy_package_hash(
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            rules_hash=rules_hash,
            strategy_code_hash=code_hash,
        ),
    )


def main() -> int:
    """Print an auditable identity for manifest packaging and review tooling."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategy-id", required=True)
    parser.add_argument("--strategy-version", required=True)
    parser.add_argument("--rules-hash", required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            strategy_package_identity(
                strategy_id=args.strategy_id,
                strategy_version=args.strategy_version,
                rules_hash=args.rules_hash,
            ).snapshot_binding(),
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
