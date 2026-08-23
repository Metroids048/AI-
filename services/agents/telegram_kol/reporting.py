from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .pipeline import PipelineResult


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if hasattr(value, "__dataclass_fields__"):
        return _jsonable(asdict(value))
    return value


def write_pipeline_outputs(result: PipelineResult, output_dir: str | Path) -> dict[str, Path]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    events_path = root / "trade_events.jsonl"
    threads_path = root / "trade_threads.json"
    report_path = root / "poc_report.md"

    with events_path.open("w", encoding="utf-8") as handle:
        for event in result.events:
            handle.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")

    threads_path.write_text(
        json.dumps([_jsonable(thread) for thread in result.threads], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    summary_lines = ["# TELEGRAM_KOL_MVP", "", "## Summary", ""]
    for key, value in result.summary.items():
        summary_lines.append(f"- {key}: {value}")
    summary_lines.extend(["", "## Events", ""])
    for event in result.events:
        summary_lines.append(
            f"- {event.event_type}: {event.symbol or '-'} {event.side or '-'} "
            f"({event.execution_readiness})"
        )
    report_path.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")

    return {"events": events_path, "threads": threads_path, "report": report_path}
