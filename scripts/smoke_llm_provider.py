"""Perform one observable LLM provider request without touching trading paths."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import uuid
from pathlib import Path

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.agents.llm_factory import build_configured_llm_runtime
from services.database import get_session_factory
from services.strategy_library import LlmInvocationRepository
from shared.models import LlmInvocation, LlmInvocationStage


def _hash(payload: object) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send exactly one observable LLM smoke request; never submit a trade.")
    parser.add_argument(
        "--database-url",
        default=f"sqlite:///{(ROOT / '.local_paper_console.db').as_posix()}",
        help="Invocation journal database; defaults to the desktop runtime SQLite database.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    os.environ["POSTGRES_URL"] = args.database_url
    session_factory = get_session_factory(args.database_url)
    try:
        with session_factory() as session:
            session.execute(text("SELECT 1"))
            session.execute(text("SELECT 1 FROM llm_invocations LIMIT 1"))
            session.execute(text("CREATE TEMPORARY TABLE llm_smoke_preflight (id INTEGER)"))
            session.execute(text("DROP TABLE llm_smoke_preflight"))
            session.rollback()
    except Exception as exc:  # noqa: BLE001 - no provider call is allowed without a writable journal
        print(
            json.dumps(
                {
                    "status": "journal_unavailable",
                    "error": str(exc),
                    "provider_called": False,
                    "trading_triggered": False,
                },
                ensure_ascii=False,
            )
        )
        return 3

    runtime = build_configured_llm_runtime()
    candidates = list(getattr(runtime, "runtimes", []) or [])
    selected = candidates[0] if candidates else runtime
    invocation_id = str(uuid.uuid4())
    payload = {
        "symbol": "BTC/USDT",
        "smoke": True,
        "instruction": "Connectivity smoke only. Do not make or authorize any trade.",
    }
    started = time.perf_counter()
    result: dict = {}
    error: str | None = None
    try:
        result = selected.generate_structured(
            agent_type="decision_veto_agent",
            task_type="pre_execution_veto_llm",
            payload=payload,
        )
    except Exception as exc:  # noqa: BLE001 - failure must still be journaled
        error = str(exc)
    latency_ms = max(0, int((time.perf_counter() - started) * 1000))
    usage = result.get("usage") or {}
    with session_factory() as session:
        invocation = LlmInvocationRepository(session).create_invocation(
            LlmInvocation(
                invocation_id=invocation_id,
                symbol="BTC/USDT",
                called=True,
                provider=result.get("provider"),
                model=result.get("model"),
                stage=LlmInvocationStage.SMOKE,
                status="passed" if error is None else "failed",
                input_hash=_hash(payload),
                output_hash=_hash(result) if result else None,
                latency_ms=latency_ms,
                prompt_tokens=int(usage.get("prompt_tokens") or 0),
                completion_tokens=int(usage.get("completion_tokens") or 0),
                total_tokens=int(usage.get("total_tokens") or 0),
                error=error,
            )
        )
    print(
        json.dumps(
            {
                "invocation_id": invocation.invocation_id,
                "provider": invocation.provider,
                "model": invocation.model,
                "prompt_tokens": invocation.prompt_tokens,
                "completion_tokens": invocation.completion_tokens,
                "total_tokens": invocation.total_tokens,
                "latency_ms": invocation.latency_ms,
                "status": invocation.status,
                "error": invocation.error,
                "trading_triggered": False,
            },
            ensure_ascii=False,
        )
    )
    return 0 if error is None else 2


if __name__ == "__main__":
    sys.exit(main())
