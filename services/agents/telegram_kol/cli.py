from __future__ import annotations

import argparse
import json
from pathlib import Path

from .pipeline import MessageEnvelope, run_pipeline
from .reporting import write_pipeline_outputs


def run_jsonl_file(input_path: str | Path, output_dir: str | Path) -> dict[str, int]:
    messages: list[MessageEnvelope] = []
    source = Path(input_path)
    for line_number, raw_line in enumerate(source.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw_line.strip():
            continue
        payload = json.loads(raw_line)
        source_chat = str(payload.get("source_chat") or "").strip()
        if not source_chat:
            raise ValueError(f"line {line_number}: source_chat is required")
        image_path = payload.get("image_path")
        messages.append(
            MessageEnvelope(
                source_chat=source_chat,
                text=str(payload.get("text") or "") or None,
                image_path=Path(image_path) if image_path else None,
            )
        )
    result = run_pipeline(messages)
    write_pipeline_outputs(result, output_dir)
    return result.summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Parse Telegram KOL messages into structured trade events")
    parser.add_argument("--input", required=True, help="JSONL message input")
    parser.add_argument("--output-dir", required=True, help="directory for trade_events.jsonl and report")
    args = parser.parse_args()
    summary = run_jsonl_file(args.input, args.output_dir)
    print(json.dumps({"status": "TELEGRAM_KOL_MVP", "summary": summary}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
