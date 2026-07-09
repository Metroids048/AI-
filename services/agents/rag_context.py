"""Lightweight local RAG snippets for LLM veto prompts."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

PACKAGE_ROOT = Path(__file__).resolve().parents[2]
STRATEGY_LIBRARY_ROOT = PACKAGE_ROOT / "策略库"
ASSET_ROOT = PACKAGE_ROOT / "research_source" / "open_source_strategy_library" / "assets"
SEED_MANIFEST = PACKAGE_ROOT / "research_source" / "open_source_strategy_library" / "manifests" / "seed_sources.json"
PREVIEW_CHARS = 240
MAX_SNIPPETS = 2


def collect_rag_snippets(
    *,
    core_thesis: str,
    entry_rules: dict[str, Any] | None = None,
    symbol: str = "",
    limit: int = MAX_SNIPPETS,
) -> list[dict[str, str]]:
    """Return top matching local strategy-library previews for veto context."""
    keywords = _keywords(core_thesis=core_thesis, entry_rules=entry_rules or {}, symbol=symbol)
    if not keywords:
        return []
    scored: list[tuple[int, dict[str, str]]] = []
    for asset in _asset_previews():
        score = sum(1 for keyword in keywords if keyword in asset["search_text"])
        if score > 0:
            scored.append((score, asset))
    scored.sort(key=lambda item: (item[0], item[1]["source_rank"]), reverse=True)
    return [
        {
            "source_id": item["source_id"],
            "path": item["path"],
            "preview": item["preview"],
        }
        for _, item in scored[: max(1, limit)]
    ]


def _keywords(*, core_thesis: str, entry_rules: dict[str, Any], symbol: str) -> set[str]:
    blob = " ".join(
        [
            core_thesis,
            json.dumps(entry_rules, ensure_ascii=False),
            symbol.replace("/", " "),
        ]
    ).lower()
    tokens = {token for token in re.split(r"[^a-z0-9_]+", blob) if len(token) >= 3}
    for tag in (
        "macd",
        "funding",
        "carry",
        "arbitrage",
        "trend",
        "momentum",
        "breakout",
        "false_breakout",
        "fake_breakout",
        "rsi",
        "adx",
        "ema",
        "vwap",
        "bollinger",
        "btc",
        "eth",
        "资金费率",
        "套利",
        "趋势",
        "假突破",
        "假跌破",
        "缠论",
        "买卖点",
        "风控",
    ):
        if tag in blob:
            tokens.add(tag)
    return tokens


@lru_cache(maxsize=1)
def _asset_previews() -> tuple[dict[str, str], ...]:
    previews: list[dict[str, str]] = []
    previews.extend(_strategy_library_previews())
    previews.extend(_open_source_asset_previews())
    return tuple(previews)


def _strategy_library_previews() -> list[dict[str, str]]:
    previews: list[dict[str, str]] = []
    if not STRATEGY_LIBRARY_ROOT.exists():
        return previews
    for path in sorted(STRATEGY_LIBRARY_ROOT.glob("*.md")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        preview = " ".join(text.split())[:PREVIEW_CHARS]
        search_text = f"strategy_library {path.stem} {path.name} {preview} {text[:1200]}".lower()
        previews.append(
            {
                "source_id": "strategy_library",
                "path": path.relative_to(PACKAGE_ROOT).as_posix(),
                "preview": preview,
                "search_text": search_text,
                "source_rank": "2",
            }
        )
    return previews


def _open_source_asset_previews() -> list[dict[str, str]]:
    previews: list[dict[str, str]] = []
    if not ASSET_ROOT.exists():
        return previews
    source_ids = _source_ids()
    if not source_ids:
        source_ids = [path.name for path in ASSET_ROOT.iterdir() if path.is_dir()]
    for source_id in source_ids:
        source_dir = ASSET_ROOT / source_id
        if not source_dir.is_dir():
            continue
        for path in sorted(source_dir.rglob("*.md")):
            text = path.read_text(encoding="utf-8", errors="ignore")
            preview = " ".join(text.split())[:PREVIEW_CHARS]
            search_text = f"{source_id} {path.name} {preview} {text[:1200]}".lower()
            previews.append(
                {
                    "source_id": source_id,
                    "path": path.relative_to(PACKAGE_ROOT).as_posix(),
                    "preview": preview,
                    "search_text": search_text,
                    "source_rank": "1",
                }
            )
    return previews


def _source_ids() -> list[str]:
    if not SEED_MANIFEST.exists():
        return []
    payload = json.loads(SEED_MANIFEST.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return [str(item.get("source_id", "")) for item in payload if isinstance(item, dict) and item.get("source_id")]
    if isinstance(payload, dict):
        return [
            str(item.get("source_id", ""))
            for item in payload.get("sources", [])
            if isinstance(item, dict) and item.get("source_id")
        ]
    return []
