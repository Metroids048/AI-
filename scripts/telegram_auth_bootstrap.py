from __future__ import annotations

import argparse
import asyncio
import json

from services.agents.telegram_kol.auth_bootstrap import authorize_and_verify
from services.agents.telegram_kol.ingestion.telegram_client import TelethonTelegramClient
from shared.config import settings


def _missing_config() -> list[str]:
    missing: list[str] = []
    if settings.telegram_api_id <= 0:
        missing.append("TELEGRAM_API_ID")
    if not settings.telegram_api_hash.strip():
        missing.append("TELEGRAM_API_HASH")
    if not settings.telegram_phone.strip():
        missing.append("TELEGRAM_PHONE")
    return missing


async def _run(folder_name: str) -> int:
    missing = _missing_config()
    if missing:
        print(f"CONFIG_ERROR missing={','.join(missing)}")
        print(
            "Fill the missing values in the local .env file. Never paste API hash, OTP, 2FA password, or session files into chat."
        )
        return 2

    client = TelethonTelegramClient(
        api_id=settings.telegram_api_id,
        api_hash=settings.telegram_api_hash,
        phone=settings.telegram_phone,
        session_dir=settings.telegram_session_dir,
    )
    try:
        result = await authorize_and_verify(client=client, folder_name=folder_name)
    except Exception as exc:  # noqa: BLE001
        print(f"TELEGRAM_BOOTSTRAP_ERROR type={type(exc).__name__}")
        return 3

    payload = {
        "status": result.status,
        "folder_name": result.folder_name,
        "source_count": result.source_count,
        "sources": [
            {
                "chat_id": source.get("chat_id"),
                "title": source.get("title"),
                "chat_type": source.get("chat_type"),
            }
            for source in result.sources
        ],
        "available_folders": list(result.available_folders),
        "session_dir": settings.telegram_session_dir,
        "collector_enabled": settings.telegram_collector_enabled,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if result.status == "READ_ONLY_VERIFY_OK" else 4


def main() -> int:
    parser = argparse.ArgumentParser(
        description="One-time Telegram User API authorization and read-only folder verification."
    )
    parser.add_argument(
        "--folder",
        default=settings.telegram_folder_name,
        help="Telegram chat-folder title to verify (defaults to TELEGRAM_FOLDER_NAME).",
    )
    args = parser.parse_args()
    return asyncio.run(_run(args.folder))


if __name__ == "__main__":
    raise SystemExit(main())
