"""Read-only runtime evidence verifier for P1 same-cycle Research Shadow."""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

RESEARCH_CANDIDATE_IDS = frozenset(
    {
        "trend_pullback_v2",
        "range_sweep_reversion_v1",
        "failed_breakout_reversal_v1",
    }
)


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if not text:
            return None
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _safe_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        decoded = json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _is_same_cycle(
    *,
    cycle: sqlite3.Row | None,
    decision_cycle_id: str,
    shadow: dict[str, Any],
    observation: dict[str, Any],
    top_level_reference: Any,
) -> bool:
    if cycle is None:
        return False
    if str(cycle["execution_mode"]).upper() != "BINANCE_TESTNET":
        return False
    cycle_id = str(cycle["cycle_id"])
    if decision_cycle_id != cycle_id or str(shadow.get("cycle_id")) != cycle_id:
        return False
    if observation.get("cycle_id") != cycle_id:
        return False
    if shadow.get("lane") != "RESEARCH_SHADOW" or observation.get("lane") != "RESEARCH_SHADOW":
        return False
    if not shadow.get("scheduler_cycle_id") or not shadow.get("scheduler_session_id"):
        return False
    if observation.get("scheduler_cycle_id") != shadow.get("scheduler_cycle_id"):
        return False
    if observation.get("scheduler_session_id") != shadow.get("scheduler_session_id"):
        return False
    if str(cycle["symbol"]) != str(shadow.get("symbol")):
        return False
    if str(shadow.get("symbol")) != str(observation.get("symbol")):
        return False
    cycle_bar = _parse_datetime(cycle["bar_timestamp"])
    shadow_bar = _parse_datetime(shadow.get("bar_close_time"))
    observation_bar = _parse_datetime(observation.get("bar_close_time"))
    if cycle_bar is None or cycle_bar != shadow_bar or shadow_bar != observation_bar:
        return False
    reference = shadow.get("market_snapshot_reference")
    return bool(reference) and reference == observation.get("market_snapshot_reference") == top_level_reference


def _valid_shadow_envelope(payload: dict[str, Any], shadow: dict[str, Any], observations: list[Any]) -> bool:
    typed_observations = [item for item in observations if isinstance(item, dict)]
    strategy_ids = {str(item.get("strategy_id")) for item in typed_observations}
    return bool(
        len(typed_observations) == len(RESEARCH_CANDIDATE_IDS)
        and strategy_ids == RESEARCH_CANDIDATE_IDS
        and shadow.get("schema_version") == "p1-same-cycle-research-shadow-v1"
        and shadow.get("lane") == "RESEARCH_SHADOW"
        and shadow.get("scheduler_session_id")
        and shadow.get("scheduler_cycle_id")
        and shadow.get("cycle_id")
        and shadow.get("symbol")
        and shadow.get("bar_close_time")
        and payload.get("active_strategy_id") == "testnet_sampling_v2"
        and shadow.get("active_strategy_id") == "testnet_sampling_v2"
        and bool(shadow.get("market_snapshot_reference"))
        and shadow.get("market_snapshot_reference") == payload.get("market_snapshot_reference")
    )


def _research_lineage(candidate_key: Any, candidate_type: Any) -> bool:
    if str(candidate_type or "").upper() == "RESEARCH":
        return True
    key = str(candidate_key or "")
    return any(
        key == strategy_id
        or key.startswith(f"{strategy_id}:")
        or key.startswith(f"{strategy_id}/")
        or key.endswith(f":{strategy_id}")
        or key.endswith(f"/{strategy_id}")
        for strategy_id in RESEARCH_CANDIDATE_IDS
    )


def _lineage_mutation_ledger(connection: sqlite3.Connection, cutover: datetime) -> dict[str, int]:
    intent_rows = connection.execute(
        "SELECT intent_id, candidate_key, candidate_type, created_at FROM v2_execution_intents"
    ).fetchall()
    research_intent_ids = {
        str(row["intent_id"]) for row in intent_rows if _research_lineage(row["candidate_key"], row["candidate_type"])
    }

    order_rows = connection.execute("SELECT intent_id, created_at FROM v2_exchange_orders").fetchall()
    position_rows = connection.execute(
        "SELECT intent_id, projected_at, protected_at, closed_at, version FROM v2_managed_positions"
    ).fetchall()
    protection_rows = connection.execute(
        "SELECT position_id, created_at, activated_at, version FROM v2_protection_records"
    ).fetchall()
    research_position_ids = {
        str(row["position_id"])
        for row in connection.execute("SELECT position_id, intent_id FROM v2_managed_positions").fetchall()
        if str(row["intent_id"]) in research_intent_ids
    }

    def after(value: Any) -> bool:
        parsed = _parse_datetime(value)
        return parsed is not None and parsed >= cutover

    return {
        "intent_created": sum(
            1 for row in intent_rows if str(row["intent_id"]) in research_intent_ids and after(row["created_at"])
        ),
        "exchange_orders_created": sum(
            1 for row in order_rows if str(row["intent_id"]) in research_intent_ids and after(row["created_at"])
        ),
        "positions_created": sum(
            1 for row in position_rows if str(row["intent_id"]) in research_intent_ids and after(row["projected_at"])
        ),
        "positions_modified": sum(
            1
            for row in position_rows
            if str(row["intent_id"]) in research_intent_ids
            and int(row["version"] or 0) > 0
            and (after(row["protected_at"]) or after(row["closed_at"]))
        ),
        "protection_orders_created": sum(
            1
            for row in protection_rows
            if str(row["position_id"]) in research_position_ids and after(row["created_at"])
        ),
        "protection_orders_modified": sum(
            1
            for row in protection_rows
            if str(row["position_id"]) in research_position_ids
            and int(row["version"] or 0) > 0
            and after(row["activated_at"])
        ),
    }


def collect_evidence(database: Path, *, cutover: datetime) -> dict[str, Any]:
    """Collect P1 evidence without opening a writable database connection."""
    resolved = database.expanduser().resolve()
    uri = f"file:{resolved.as_posix()}?mode=ro"
    counts = {"before_shadow_observations": 0, "after_shadow_observations": 0}
    matches = 0
    examples: list[dict[str, Any]] = []

    with sqlite3.connect(uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        cycles = {
            str(row["cycle_id"]): row
            for row in connection.execute(
                "SELECT cycle_id, symbol, bar_timestamp, execution_mode FROM v2_execution_cycles"
            ).fetchall()
        }
        decisions = connection.execute(
            "SELECT cycle_id, payload, created_at FROM v2_execution_decisions ORDER BY created_at"
        ).fetchall()
        for decision in decisions:
            payload = _safe_json(decision["payload"])
            shadow = payload.get("research_shadow")
            if not isinstance(shadow, dict):
                continue
            observations = shadow.get("observations")
            if not isinstance(observations, list):
                continue
            created_at = _parse_datetime(decision["created_at"])
            if created_at is None:
                continue
            phase = "before_shadow_observations" if created_at < cutover else "after_shadow_observations"
            phase_observations = [item for item in observations if isinstance(item, dict)]
            valid_envelope = _valid_shadow_envelope(payload, shadow, observations)
            counts[phase] += len(phase_observations)
            cycle = cycles.get(str(decision["cycle_id"]))
            testnet_cycle = cycle is not None and str(cycle["execution_mode"]).upper() == "BINANCE_TESTNET"
            if created_at >= cutover and valid_envelope and testnet_cycle and len(examples) < 2:
                examples.append(
                    {
                        "cycle_id": str(decision["cycle_id"]),
                        "symbol": shadow.get("symbol"),
                        "bar_close": shadow.get("bar_close_time"),
                        "active_decision": payload.get("active_decision"),
                        "active_terminal_reason": payload.get("active_terminal_reason"),
                        "shadow_decisions": {
                            str(item.get("strategy_id")): item.get("decision_status")
                            for item in phase_observations
                            if item.get("strategy_id")
                        },
                    }
                )
            if created_at < cutover:
                continue
            for observation in phase_observations:
                if (
                    testnet_cycle
                    and valid_envelope
                    and _is_same_cycle(
                        cycle=cycle,
                        decision_cycle_id=str(decision["cycle_id"]),
                        shadow=shadow,
                        observation=observation,
                        top_level_reference=payload.get("market_snapshot_reference"),
                    )
                ):
                    matches += 1

        ledger = _lineage_mutation_ledger(connection, cutover)

    return {
        "database": str(resolved),
        "cutover_utc": cutover.isoformat(),
        "change_effect": {
            **counts,
            "same_cycle_matches": matches,
            "unmatched": counts["after_shadow_observations"] - matches,
        },
        "examples": examples,
        "mutation_ledger": ledger,
    }


def _parse_cutover(value: str) -> datetime:
    parsed = _parse_datetime(value)
    if parsed is None:
        raise ValueError("cutover must be an ISO-8601 UTC timestamp")
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("--cutover-utc", required=True)
    args = parser.parse_args()
    evidence = collect_evidence(args.database, cutover=_parse_cutover(args.cutover_utc))
    print(json.dumps(evidence, indent=2, sort_keys=True))
    effect = evidence["change_effect"]
    ledger = evidence["mutation_ledger"]
    accepted = (
        effect["after_shadow_observations"] > 0
        and effect["same_cycle_matches"] > 0
        and effect["unmatched"] == 0
        and all(value == 0 for value in ledger.values())
    )
    return 0 if accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
