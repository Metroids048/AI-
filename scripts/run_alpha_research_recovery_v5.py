"""Read-only Telegram KOL history audit for ALPHA_RESEARCH_RECOVERY_V5.

The V5 first gate is deliberately separated from research execution.  When the
existing local Telegram User API session cannot expose enough history, this
runner emits a complete, non-sensitive evidence package and stops before any
signal ledger, market replay, or runtime integration is attempted.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from services.agents.telegram_kol.domain.events import KolEventType
from services.agents.telegram_kol.domain.messages import MessageEnvelope
from services.agents.telegram_kol.ingestion.folder_resolver import resolve_folder_chats
from services.agents.telegram_kol.ingestion.telegram_client import (
    TelegramAuthRequired,
    TelethonTelegramClient,
)
from services.agents.telegram_kol.parsing.parser import UniversalKolParser
from shared.config import settings

STATUS_BLOCKED = "BLOCKED_KOL_HISTORICAL_DATA"
MIN_HISTORY_DAYS = 180
MIN_SIGNAL_LIKE_MESSAGES = 100
DEFAULT_HISTORY_LIMIT = 5000

_NOT_RUN_ARTIFACTS = (
    "RAW_MESSAGE_MANIFEST.json",
    "PARSER_QA.json",
    "H1_DIRECT_RESULT.json",
    "H2_CONSENSUS_RESULT.json",
    "H3_QUALITY_RESULT.json",
    "SOURCE_ATTRIBUTION.json",
    "SYMBOL_ATTRIBUTION.json",
    "LATENCY_STRESS.json",
    "VALIDATION_RESULTS.json",
)


def _json_dump(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def _utc(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _peer_id(peer: Any) -> str:
    try:
        from telethon.utils import get_peer_id

        return str(get_peer_id(peer))
    except Exception:  # noqa: BLE001 - optional Telethon runtime
        pass
    for attribute in ("channel_id", "chat_id", "user_id", "id"):
        value = getattr(peer, attribute, None)
        if value is not None:
            return str(value)
    return str(peer)


def _filter_mapping(item: Any) -> dict[str, Any]:
    title = getattr(item, "title", None)
    title = getattr(title, "text", title)
    peers = getattr(item, "include_peers", None) or getattr(item, "pinned_peers", None) or ()
    return {"title": str(title or ""), "chat_ids": [_peer_id(peer) for peer in peers]}


def _dialog_mapping(item: Any) -> dict[str, Any]:
    entity = getattr(item, "entity", item)
    chat_id = getattr(item, "id", None) or getattr(entity, "id", None)
    title = getattr(item, "name", None) or getattr(entity, "title", None) or getattr(entity, "first_name", None)
    return {"chat_id": str(chat_id), "title": str(title or chat_id), "chat_type": type(entity).__name__}


def _message_text(message: Any) -> str:
    return str(getattr(message, "message", "") or getattr(message, "text", "") or getattr(message, "caption", "") or "")


def _is_signal_like(*, source_id: str, chat_id: str, message: Any, posted_at: datetime) -> bool:
    text = _message_text(message)
    if not text.strip():
        return False
    envelope = MessageEnvelope(
        source_id=source_id,
        chat_id=chat_id,
        message_id=int(getattr(message, "id", 0) or 0),
        revision=0,
        posted_at=posted_at,
        received_at=posted_at,
        text=text,
    )
    event = UniversalKolParser().parse(envelope)
    return event.event_type is KolEventType.OPEN and event.symbol is not None and event.side is not None


def _config_audit() -> dict[str, Any]:
    session_dir = Path(settings.telegram_session_dir).resolve()
    session_files = sorted(path.name for path in session_dir.glob("telegram_kol*") if path.is_file())
    return {
        "collector_enabled": bool(settings.telegram_collector_enabled),
        "api_id_configured": bool(settings.telegram_api_id),
        "api_credentials_configured": bool(settings.telegram_api_id and settings.telegram_api_hash),
        "contact_configured": bool(settings.telegram_phone),
        "session_dir": str(session_dir),
        "session_files_present": bool(session_files),
        "session_file_count": len(session_files),
    }


def _gate_passed(groups: Iterable[dict[str, Any]]) -> bool:
    for group in groups:
        if not group.get("history_accessible"):
            continue
        first = group.get("first_message_at")
        last = group.get("last_message_at")
        if not first or not last:
            continue
        try:
            span_days = (datetime.fromisoformat(str(last)) - datetime.fromisoformat(str(first))).total_seconds() / 86400
        except ValueError:
            continue
        if span_days >= MIN_HISTORY_DAYS and int(group.get("signal_like_message_count", 0)) >= MIN_SIGNAL_LIKE_MESSAGES:
            return True
    return False


async def audit_telegram(*, history_limit: int = DEFAULT_HISTORY_LIMIT) -> dict[str, Any]:
    """Inspect existing read-only Telegram access without prompting or sending messages."""

    config = _config_audit()
    base: dict[str, Any] = {
        "audit_version": "alpha_research_recovery_v5",
        "name": "KOL_SIGNAL_ALPHA",
        "audited_at": datetime.now(UTC).isoformat(),
        "read_only": True,
        "config": config,
        "minimum_gate": {
            "history_days": MIN_HISTORY_DAYS,
            "signal_like_messages": MIN_SIGNAL_LIKE_MESSAGES,
            "at_least_one_real_source": True,
        },
        "accessible_groups": [],
        "status": STATUS_BLOCKED,
        "blocker": "TELEGRAM_AUTH_REQUIRED",
    }
    if not config["collector_enabled"] or not config["api_credentials_configured"]:
        base["blocker"] = "TELEGRAM_CREDENTIALS_NOT_CONFIGURED"
        return base
    if not config["session_files_present"]:
        base["blocker"] = "TELEGRAM_SESSION_NOT_FOUND"
        return base

    client: TelethonTelegramClient | None = None
    try:
        client = TelethonTelegramClient(
            api_id=settings.telegram_api_id,
            api_hash=settings.telegram_api_hash,
            phone=settings.telegram_phone,
            session_dir=settings.telegram_session_dir,
        )
        await client.connect()
        if not await client.is_user_authorized():
            base["blocker"] = "TELEGRAM_AUTH_REQUIRED"
            return base
        filters = [_filter_mapping(item) async for item in client.iter_dialog_filters()]
        dialogs = [_dialog_mapping(item) async for item in client.iter_dialogs()]
        sources = resolve_folder_chats(settings.telegram_folder_name, filters=filters, dialogs=dialogs)
        if not sources:
            base["blocker"] = "TELEGRAM_FOLDER_EMPTY_OR_NOT_FOUND"
            return base
        for source in sources:
            source_id = str(source.get("source_id") or source.get("chat_id"))
            chat_id = str(source.get("chat_id"))
            first: datetime | None = None
            last: datetime | None = None
            count = 0
            signal_like = 0
            try:
                async for message in client.iter_messages(chat_id, limit=history_limit):
                    count += 1
                    posted_at = _utc(getattr(message, "date", None))
                    if posted_at is None:
                        continue
                    first = posted_at if first is None or posted_at < first else first
                    last = posted_at if last is None or posted_at > last else last
                    if _is_signal_like(source_id=source_id, chat_id=chat_id, message=message, posted_at=posted_at):
                        signal_like += 1
            except Exception as exc:  # noqa: BLE001 - per-group access is evidence
                base["accessible_groups"].append(
                    {
                        "group_id": chat_id,
                        "title": str(source.get("title") or chat_id),
                        "first_message_at": first.isoformat() if first else None,
                        "last_message_at": last.isoformat() if last else None,
                        "message_count": count,
                        "history_accessible": False,
                        "signal_like_message_count": signal_like,
                        "error_type": type(exc).__name__,
                    }
                )
                continue
            base["accessible_groups"].append(
                {
                    "group_id": chat_id,
                    "title": str(source.get("title") or chat_id),
                    "first_message_at": first.isoformat() if first else None,
                    "last_message_at": last.isoformat() if last else None,
                    "message_count": count,
                    "history_accessible": True,
                    "signal_like_message_count": signal_like,
                    "history_limit": history_limit,
                }
            )
        if _gate_passed(base["accessible_groups"]):
            base["status"] = "KOL_HISTORY_GATE_PASSED"
            base["blocker"] = None
        else:
            base["blocker"] = "MINIMUM_HISTORY_GATE_NOT_MET"
        return base
    except (TelegramAuthRequired, OSError) as exc:
        base["blocker"] = type(exc).__name__
        return base
    except Exception as exc:  # noqa: BLE001 - audit must leave a durable blocker
        base["blocker"] = f"TELEGRAM_AUDIT_ERROR:{type(exc).__name__}"
        return base
    finally:
        if client is not None:
            await client.disconnect()


def _write_empty_signal_ledger(path: Path) -> None:
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ModuleNotFoundError:
        fallback = Path.home() / ".agent-reach-venv" / "Lib" / "site-packages"
        if not fallback.exists():
            return
        import sys

        sys.path.insert(0, str(fallback))
        import pyarrow as pa
        import pyarrow.parquet as pq
    schema = pa.schema(
        [
            ("signal_id", pa.string()),
            ("source_id", pa.string()),
            ("message_id", pa.int64()),
            ("published_at", pa.string()),
            ("symbol", pa.string()),
            ("side", pa.string()),
            ("entry_type", pa.string()),
            ("entry_low", pa.float64()),
            ("entry_high", pa.float64()),
            ("stop_loss", pa.float64()),
            ("take_profits", pa.list_(pa.float64())),
            ("parser_status", pa.string()),
        ]
    )
    pq.write_table(pa.Table.from_pylist([], schema=schema), path)


def write_blocked_artifacts(*, output_dir: Path, audit: dict[str, Any]) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    _json_dump(output_dir / "TELEGRAM_DATA_AUDIT.json", audit)
    reason = str(audit.get("blocker") or STATUS_BLOCKED)
    for name in _NOT_RUN_ARTIFACTS:
        _json_dump(
            output_dir / name,
            {
                "status": "NOT_RUN",
                "reason": STATUS_BLOCKED,
                "blocker_detail": reason,
                "final_holdout_accessed": False,
                "runtime_modified": False,
                "production_authority": "NOT_GRANTED",
            },
        )
    _write_empty_signal_ledger(output_dir / "SIGNAL_LEDGER.parquet")
    plan = {
        "version": "alpha_research_recovery_v5",
        "name": "KOL_SIGNAL_ALPHA",
        "mode": "historical_read_only",
        "runtime_frozen": True,
        "runtime_modified": False,
        "final_holdout_accessed": False,
        "minimum_history_days": MIN_HISTORY_DAYS,
        "minimum_signal_like_messages": MIN_SIGNAL_LIKE_MESSAGES,
        "status": STATUS_BLOCKED,
        "next_step": "Obtain operator-authorized read-only Telegram session and rerun this audit; do not synthesize signals.",
    }
    _json_dump(output_dir / "RESEARCH_PLAN.json", plan)
    report = {
        "status": STATUS_BLOCKED,
        "telegram": {
            "sources": audit.get("accessible_groups", []),
            "history_gate": "FAIL",
            "blocker": reason,
        },
        "parser": {"status": "NOT_RUN", "unsafe_edited_messages": 0},
        "h1": {"status": "NOT_RUN"},
        "h2": {"status": "NOT_RUN"},
        "h3": {"status": "NOT_RUN"},
        "latency": {"status": "NOT_RUN", "scenarios_seconds": [10, 30, 60]},
        "survivor": None,
        "runtime_during_research": {"status": "READ_ONLY_OBSERVATION_NOT_STARTED"},
        "final_holdout_accessed": False,
        "runtime_modified": False,
        "production": "NOT_GRANTED",
        "required_to_resume": {
            "real_kol_sources": 1,
            "minimum_history_days": MIN_HISTORY_DAYS,
            "minimum_signal_like_messages": MIN_SIGNAL_LIKE_MESSAGES,
        },
    }
    _json_dump(output_dir / "FINAL_REPORT.json", report)
    return report


async def _run(args: argparse.Namespace) -> int:
    audit = await audit_telegram(history_limit=args.history_limit)
    report = write_blocked_artifacts(output_dir=args.output_dir, audit=audit)
    print(json.dumps({"status": report["status"], "blocker": audit.get("blocker")}, indent=2))
    return 2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("artifacts/alpha_research_recovery_v5"),
    )
    parser.add_argument("--history-limit", type=int, default=DEFAULT_HISTORY_LIMIT)
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
