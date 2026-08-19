"""Point-in-time dataset export shared by all research engines."""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .contracts import stable_hash


def export_canonical_dataset(
    rows: Iterable[dict[str, Any]],
    *,
    dataset_id: str,
    output_path: str | Path,
    source_database: str | None = None,
    symbols: list[str] | None = None,
    timeframes: list[str] | None = None,
    window: dict[str, Any] | None = None,
    cost_model: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ordered_rows = [dict(row) for row in rows]
    ordered_rows.sort(
        key=lambda row: (
            str(row.get("symbol", "")),
            str(row.get("timestamp", row.get("observed_at", ""))),
        )
    )
    manifest = {
        "dataset_id": dataset_id,
        "created_at": datetime.now(UTC).isoformat(),
        "source_database": source_database,
        "symbols": symbols or sorted({str(row.get("symbol")) for row in ordered_rows if row.get("symbol")}),
        "timeframes": timeframes or sorted({str(row.get("timeframe")) for row in ordered_rows if row.get("timeframe")}),
        "window": window or {},
        "bar_count": len(ordered_rows),
        "rows": ordered_rows,
        "cost_model": cost_model or {},
    }
    manifest["dataset_hash"] = stable_hash({key: value for key, value in manifest.items() if key != "created_at"})
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=True, sort_keys=True, indent=2, default=str), encoding="utf-8")
    return manifest


def load_canonical_dataset(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not payload.get("dataset_hash"):
        raise ValueError("canonical dataset is missing dataset_hash")
    expected = stable_hash({key: value for key, value in payload.items() if key not in {"dataset_hash", "created_at"}})
    if payload["dataset_hash"] != expected:
        raise ValueError("canonical dataset hash mismatch")
    return payload
