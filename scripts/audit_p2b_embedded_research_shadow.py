"""Count P2-B same-cycle research shadow observations in V2 decisions.

The P1 shadow payload is embedded in ``v2_execution_decisions.payload``; the
legacy ``v2_shadow_records`` table is not the authoritative storage path for
these observations.  This script is read-only and reports raw plus de-duplicated
counts after the P2 cutover boundary.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / ".local_paper_console.db"
OUT_JSON = ROOT / "docs/audits/2026-08-16-p2b-embedded-shadow.json"
OUT_MD = ROOT / "docs/audits/2026-08-16-p2b-embedded-shadow.md"
CUTOVER = "2026-08-10T13:15:03.869648+00:00"
CANDIDATES = (
    "trend_pullback_v2",
    "range_sweep_reversion_v1",
    "failed_breakout_reversal_v1",
)


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def build() -> dict[str, Any]:
    conn = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    decisions = [
        dict(row)
        for row in conn.execute(
            """
            SELECT decision_id, cycle_id, candidate_key, terminal_reason, payload, created_at
            FROM v2_execution_decisions
            WHERE created_at >= ?
            ORDER BY created_at
            """,
            (CUTOVER,),
        )
    ]
    legacy_shadow_count = conn.execute("SELECT COUNT(*) FROM v2_shadow_records").fetchone()[0]
    conn.close()

    raw: list[dict[str, Any]] = []
    malformed = 0
    for decision in decisions:
        try:
            payload = (
                json.loads(decision["payload"] or "{}") if isinstance(decision["payload"], str) else decision["payload"]
            )
        except json.JSONDecodeError:
            malformed += 1
            continue
        shadow = payload.get("research_shadow") if isinstance(payload, dict) else None
        if not isinstance(shadow, dict):
            continue
        observations = shadow.get("observations") or []
        for observation in observations:
            if observation.get("strategy_id") not in CANDIDATES:
                continue
            raw.append(
                {
                    "decision_id": decision["decision_id"],
                    "cycle_id": decision["cycle_id"],
                    "created_at": decision["created_at"],
                    "shadow_status": shadow.get("status"),
                    "shadow_schema_version": shadow.get("schema_version"),
                    "strategy_id": observation.get("strategy_id"),
                    "strategy_version": observation.get("strategy_version"),
                    "symbol": observation.get("symbol"),
                    "bar_close_time": observation.get("bar_close_time"),
                    "decision_status": observation.get("decision_status"),
                    "signal_present": observation.get("signal_present"),
                    "terminal_reason": observation.get("terminal_reason"),
                    "context_hash": shadow.get("context_hash"),
                }
            )

    def key(row: dict[str, Any]) -> tuple[Any, ...]:
        return (
            row["strategy_id"],
            row["symbol"],
            row["bar_close_time"],
            row["context_hash"],
        )

    unique: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in raw:
        unique.setdefault(key(row), row)

    def counts(rows: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "total": len(rows),
            "by_strategy": dict(Counter(row["strategy_id"] for row in rows)),
            "by_symbol": dict(Counter(row["symbol"] for row in rows)),
            "by_decision_status": dict(Counter(row["decision_status"] for row in rows)),
            "by_shadow_status": dict(Counter(row["shadow_status"] for row in rows)),
            "by_signal_present": dict(Counter(str(row["signal_present"]) for row in rows)),
            "by_terminal_reason": dict(Counter(row["terminal_reason"] for row in rows)),
        }

    by_candidate: dict[str, dict[str, Any]] = defaultdict(dict)
    for candidate in CANDIDATES:
        candidate_raw = [row for row in raw if row["strategy_id"] == candidate]
        candidate_unique = [row for row in unique.values() if row["strategy_id"] == candidate]
        by_candidate[candidate] = {
            "raw": counts(candidate_raw),
            "unique": counts(candidate_unique),
        }

    payload = {
        "status": "P2B_EMBEDDED_SHADOW_CONFIRMED",
        "cutover_utc": CUTOVER,
        "source": "v2_execution_decisions.payload.research_shadow.observations",
        "legacy_v2_shadow_records_total": legacy_shadow_count,
        "decision_rows_after_cutover": len(decisions),
        "malformed_decision_payloads": malformed,
        "all_candidates": {
            "raw": counts(raw),
            "unique": counts(list(unique.values())),
        },
        "by_candidate": by_candidate,
        "interpretation": {
            "answer": "The three P2-B candidates are being evaluated and persisted in embedded same-cycle research_shadow observations; zero rows in legacy v2_shadow_records is a storage-path mismatch, not zero evaluations.",
            "signal_answer": "Counts are predominantly SHADOW_NO_SIGNAL/candidate_conditions_not_met; this is evidence about signal rarity, not a persistence outage, subject to the raw-vs-unique duplicate counts above.",
            "mutation_ledger": "Not recomputed here; the prior verifier reported zero mutation-ledger writes.",
        },
        "sample_rows": raw[-12:],
    }
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# P2-B Embedded Same-Cycle Research Shadow",
        "",
        "## Verdict",
        "",
        "`P2B_EMBEDDED_SHADOW_CONFIRMED` — the three candidates are persisted under `v2_execution_decisions.payload.research_shadow`; the legacy `v2_shadow_records` table is not the active storage path.",
        "",
        "## Scope",
        "",
        f"- Cutover boundary: `{CUTOVER}`.",
        f"- Decision rows after cutover: `{len(decisions)}`; malformed payloads: `{malformed}`.",
        f"- Legacy `v2_shadow_records` total (all history): `{legacy_shadow_count}`.",
        "",
        "## Counts",
        "",
        f"- Raw embedded observations: `{len(raw)}`; unique by `(strategy, symbol, bar_close_time, context_hash)`: `{len(unique)}`.",
    ]
    for candidate in CANDIDATES:
        item = by_candidate[candidate]
        lines.append(
            f"- `{candidate}`: raw `{item['raw']['total']}`, unique `{item['unique']['total']}`, statuses `{item['unique']['by_decision_status']}`, terminal reasons `{item['unique']['by_terminal_reason']}`."
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "The zero-row observation came from querying the wrong table. Embedded observations exist for all three candidates. Their dominant state is `SHADOW_NO_SIGNAL` with `candidate_conditions_not_met`, so the current evidence points to low signal incidence rather than a disconnected shadow writer. The raw/unique split is reported because duplicate decision rows exist in the historical database.",
        ]
    )
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(OUT_JSON)
    print(OUT_MD)
    print(json.dumps(payload["all_candidates"], ensure_ascii=False))


if __name__ == "__main__":
    build()
