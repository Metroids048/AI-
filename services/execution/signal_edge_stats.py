"""Load versioned, symbol-scoped out-of-sample edge evidence."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

EDGE_STATS_ARTIFACT_DIR = Path("artifacts/signal_edge_stats")
COMMITTED_EDGE_STATS_DIR = Path("docs/evidence/active-edge-stats")
EDGE_STATS_SCHEMA_VERSION = 2


def _jsonable_rules(rules: Any) -> Any:
    if hasattr(rules, "model_dump"):
        return rules.model_dump(mode="json")
    return rules


def strategy_rules_hash(rules: Any) -> str:
    payload = json.dumps(_jsonable_rules(rules), ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def symbol_artifact_key(symbol: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", symbol.upper().split(":", maxsplit=1)[0])


@dataclass(frozen=True)
class SignalEdgeStatsArtifact:
    schema_version: int
    strategy_key: str
    candidate_id: str
    rules_hash: str
    symbol: str
    computed_at: str
    sample_count: int
    oos_sample_count: int
    win_rate: float
    average_net_win: float
    average_net_loss_magnitude: float
    net_expectancy: float
    sharpe: float
    profit_factor: float
    max_drawdown: float
    evaluation_start: str | None
    evaluation_end: str | None
    eligible: bool
    failed_reasons: tuple[str, ...]
    max_age_days: int = 30
    artifact_path: str | None = None

    @property
    def average_win(self) -> float:
        return self.average_net_win

    @property
    def average_loss(self) -> float:
        return self.average_net_loss_magnitude


def _active_pointer_path(strategy_key: str, candidate_id: str, symbol: str) -> Path:
    return EDGE_STATS_ARTIFACT_DIR / strategy_key / candidate_id / symbol_artifact_key(symbol) / "active.json"


def _committed_pointer_path(strategy_key: str, candidate_id: str, symbol: str) -> Path:
    return COMMITTED_EDGE_STATS_DIR / strategy_key / candidate_id / symbol_artifact_key(symbol) / "active.json"


def load_active_edge_stats(
    strategy_key: str,
    candidate_id: str,
    symbol: str,
    rules: Any,
    *,
    now: datetime | None = None,
) -> SignalEdgeStatsArtifact | None:
    for pointer_path in (
        _active_pointer_path(strategy_key, candidate_id, symbol),
        _committed_pointer_path(strategy_key, candidate_id, symbol),
    ):
        if not pointer_path.exists():
            continue
        artifact = _load_edge_stats_pointer(
            pointer_path,
            strategy_key=strategy_key,
            candidate_id=candidate_id,
            symbol=symbol,
            rules=rules,
            now=now,
        )
        if artifact is not None:
            return artifact
    return None


def _load_edge_stats_pointer(
    pointer_path: Path,
    *,
    strategy_key: str,
    candidate_id: str,
    symbol: str,
    rules: Any,
    now: datetime | None,
) -> SignalEdgeStatsArtifact | None:
    try:
        meta = json.loads(pointer_path.read_text(encoding="utf-8"))
        computed_at = datetime.fromisoformat(meta["computed_at"])
        max_age_days = int(meta.get("max_age_days", 30))
        reference_time = now if now is not None else datetime.now(computed_at.tzinfo)
        if (reference_time - computed_at).days > max_age_days:
            return None
        if int(meta["schema_version"]) != EDGE_STATS_SCHEMA_VERSION:
            return None
        if meta["strategy_key"] != strategy_key or meta["candidate_id"] != candidate_id:
            return None
        if symbol_artifact_key(meta["symbol"]) != symbol_artifact_key(symbol):
            return None
        if meta["rules_hash"] != strategy_rules_hash(rules) or not bool(meta["eligible"]):
            return None
        return SignalEdgeStatsArtifact(
            schema_version=int(meta["schema_version"]),
            strategy_key=meta["strategy_key"],
            candidate_id=meta["candidate_id"],
            rules_hash=meta["rules_hash"],
            symbol=meta["symbol"],
            computed_at=meta["computed_at"],
            sample_count=int(meta["sample_count"]),
            oos_sample_count=int(meta["oos_sample_count"]),
            win_rate=float(meta["win_rate"]),
            average_net_win=float(meta["average_net_win"]),
            average_net_loss_magnitude=abs(float(meta["average_net_loss_magnitude"])),
            net_expectancy=float(meta["net_expectancy"]),
            sharpe=float(meta["sharpe"]),
            profit_factor=float(meta["profit_factor"]),
            max_drawdown=float(meta["max_drawdown"]),
            evaluation_start=meta.get("evaluation_start"),
            evaluation_end=meta.get("evaluation_end"),
            eligible=bool(meta["eligible"]),
            failed_reasons=tuple(str(item) for item in meta.get("failed_reasons", [])),
            max_age_days=max_age_days,
            artifact_path=str(pointer_path),
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None


def publish_committed_edge_evidence(
    *,
    manifest_path: Path,
    rules: Any,
    source_root: Path = EDGE_STATS_ARTIFACT_DIR,
    destination_root: Path = COMMITTED_EDGE_STATS_DIR,
) -> list[Path]:
    """Publish validated local active pointers into the deployable evidence tree."""

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    strategy_key = str(manifest["strategy_key"])
    candidate_id = str(manifest["candidate_id"])
    expected_hash = strategy_rules_hash(rules)
    if int(manifest["schema_version"]) != EDGE_STATS_SCHEMA_VERSION:
        raise ValueError("unsupported active manifest schema")
    if manifest["rules_hash"] != expected_hash:
        raise ValueError("active manifest rules hash mismatch")

    published: list[Path] = []
    for symbol in [str(item) for item in manifest.get("eligible_symbols", [])]:
        source = source_root / strategy_key / candidate_id / symbol_artifact_key(symbol) / "active.json"
        artifact = _load_edge_stats_pointer(
            source,
            strategy_key=strategy_key,
            candidate_id=candidate_id,
            symbol=symbol,
            rules=rules,
            now=None,
        )
        if artifact is None:
            raise ValueError(f"eligible evidence missing or invalid for {symbol}")
        destination = destination_root / strategy_key / candidate_id / symbol_artifact_key(symbol) / "active.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        published.append(destination)
    if not published:
        raise ValueError("active manifest has no publishable eligible symbols")
    return published
