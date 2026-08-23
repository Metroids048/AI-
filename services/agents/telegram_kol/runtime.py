from __future__ import annotations

from dataclasses import replace
from typing import Any

from shared.config import settings

from .ingestion.collector import TelegramCollector
from .ingestion.folder_resolver import resolve_folder_chats
from .ingestion.media_store import MediaStore
from .ingestion.service import TelegramCollectorService
from .ingestion.telegram_client import TelegramAuthRequired, TelethonTelegramClient
from .observability.health import TelegramKolHealth
from .parsing.ocr import NullOcr
from .persistence.repository import SqlRawMessageLedger, SqlSourceRegistry


class TelegramKolRuntime:
    def __init__(self) -> None:
        self.health = TelegramKolHealth()
        self.sources: list[dict[str, Any]] = []
        self._client: TelethonTelegramClient | None = None
        self._service: TelegramCollectorService | None = None

    async def start(self) -> None:
        if not settings.telegram_collector_enabled:
            self.health = replace(self.health, blocked_reasons=("COLLECTOR_DISABLED",))
            return
        if not settings.telegram_api_id or not settings.telegram_api_hash:
            self.health = replace(self.health, blocked_reasons=("TELEGRAM_AUTH_REQUIRED",))
            return
        try:
            self._client = TelethonTelegramClient(
                api_id=settings.telegram_api_id,
                api_hash=settings.telegram_api_hash,
                phone=settings.telegram_phone,
                session_dir=settings.telegram_session_dir,
            )
            self._service = TelegramCollectorService(
                client=self._client,
                collector=TelegramCollector(
                    ledger=SqlRawMessageLedger(settings.postgres_url),
                    ocr=NullOcr(),
                ),
                folder_name=settings.telegram_folder_name,
                history_limit=settings.telegram_history_backfill_limit,
                resync_seconds=settings.telegram_folder_resync_seconds,
                media_store=MediaStore(f"{settings.telegram_session_dir}/../media"),
                source_registry=SqlSourceRegistry(settings.postgres_url),
            )
            await self._service.start()
            self.sources = list(self._service.sources)
            self.health = replace(self.health, account_connected=True, session_valid=True)
            self.health = replace(self.health, folder_found=bool(self.sources), source_count=len(self.sources))
        except TelegramAuthRequired:
            self.health = replace(self.health, blocked_reasons=("TELEGRAM_AUTH_REQUIRED",))
        except Exception as exc:  # noqa: BLE001
            self.health = replace(self.health, blocked_reasons=(f"COLLECTOR_ERROR:{type(exc).__name__}",))

    async def stop(self) -> None:
        if self._service is not None:
            await self._service.stop()
            self._service = None
        elif self._client is not None:
            await self._client.disconnect()
        self._client = None

    async def sync_folder(self) -> list[dict[str, Any]]:
        if self._client is None:
            return []
        filters = [self._to_filter_mapping(item) async for item in self._client.iter_dialog_filters()]
        dialogs = [self._to_dialog_mapping(item) async for item in self._client.iter_dialogs()]
        self.sources = resolve_folder_chats(settings.telegram_folder_name, filters=filters, dialogs=dialogs)
        self.health = replace(self.health, folder_found=bool(self.sources), source_count=len(self.sources))
        return self.sources

    def snapshot(self) -> dict[str, Any]:
        return {"status": self.health.status, "health": self.health.__dict__, "sources": list(self.sources)}

    @staticmethod
    def _to_filter_mapping(item: Any) -> dict[str, Any]:
        title = getattr(item, "title", None)
        title = getattr(title, "text", title)
        peers = getattr(item, "include_peers", None) or getattr(item, "pinned_peers", None) or ()
        return {"title": str(title or ""), "chat_ids": [TelegramKolRuntime._peer_id(peer) for peer in peers]}

    @staticmethod
    def _to_dialog_mapping(item: Any) -> dict[str, Any]:
        entity = getattr(item, "entity", item)
        chat_id = getattr(item, "id", None) or getattr(entity, "id", None)
        title = getattr(item, "name", None) or getattr(entity, "title", None) or getattr(entity, "first_name", None)
        return {"chat_id": str(chat_id), "title": str(title or chat_id), "chat_type": type(entity).__name__}

    @staticmethod
    def _peer_id(peer: Any) -> str:
        try:
            from telethon.utils import get_peer_id

            return str(get_peer_id(peer))
        except Exception:  # noqa: BLE001
            pass
        for attr in ("channel_id", "chat_id", "user_id", "id"):
            value = getattr(peer, attr, None)
            if value is not None:
                return str(value)
        return str(peer)


runtime = TelegramKolRuntime()
