from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path


class OcrUnavailable(RuntimeError):
    pass


def extract_text_from_image(path: str | Path) -> str:
    image = Path(path)
    if not image.exists():
        raise FileNotFoundError(image)
    executable = shutil.which("tesseract")
    if executable is None:
        raise OcrUnavailable("tesseract executable is not installed")
    proc = subprocess.run(
        [executable, str(image), "stdout", "-l", "chi_sim+eng", "--psm", "6"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if proc.returncode != 0:
        raise OcrUnavailable(proc.stderr.strip() or f"tesseract exited {proc.returncode}")
    return proc.stdout


def clean_ocr_text(text: str) -> str:
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if re.search(r"@Tarderfengge|QQ\s*[:：]?\s*\d+", stripped, re.I):
            continue
        stripped = stripped.replace("止僵点位", "止盈点位")
        lines.append(stripped)
    return "\n".join(lines)


def _split_ocr_blocks(text: str) -> list[str]:
    """Split a screenshot OCR stream into likely Telegram message bubbles."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        new_boundary = False
        if current:
            if re.match(r"^[A-Z]{2,10}(?:现价|市价)", line, re.I):
                new_boundary = True
            elif re.search(r"(?:合约策略|交易策略)$", line) and any("合约策略" in item for item in current):
                new_boundary = True
            elif re.match(r"^(?:锁定\d+连胜|复盘|总结)", line):
                new_boundary = True
        if new_boundary:
            blocks.append(current)
            current = []
        current.append(line)
    if current:
        blocks.append(current)
    return ["\n".join(block) for block in blocks if block]


def parse_ocr_blocks(source_chat: str, text: str):
    from .parser import parse_message

    events = []
    context_sides: dict[str, str] = {}
    for block in _split_ocr_blocks(text):
        probe = parse_message(source_chat, block)
        context_side = context_sides.get(probe.symbol or "")
        event = parse_message(source_chat, block, context_side=context_side)
        events.append(event)
        if event.symbol and event.side and event.event_type == "OPEN":
            context_sides[event.symbol] = event.side
    return events
