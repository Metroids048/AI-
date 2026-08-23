from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime
from typing import Any, Protocol

from .collector import TelegramCollector
from .folder_resolver import resolve_folder_chats
from .media_store import MediaStore
from .telegram_client import TelegramClientPort


class SourceRegistryPort(Protocol):
    def sync(self, sources: list[dict[str, object]]) -> None: ...


class TelegramCollectorService:
    """Telethon lifecycle glue: discover, backfill, then subscribe read-only."""

    def __init__(
        self,
        *,
        client: TelegramClientPort,
        collector: TelegramCollector,
        folder_name: str,
        history_limit: int = 100,
        resync_seconds: int = 300,
        media_store: MediaStore | None = None,
        source_registry: SourceRegistryPort | None = None,
    ) -> None:
        self.client = client
        self.collector = collector
        self.folder_name = folder_name
        self.history_limit = history_limit
        self.resync_seconds = resync_seconds
        self.media_store = media_store
        self.source_registry = source_registry
        self.sources: list[dict[str, object]] = []
        self._resync_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        await self.client.connect()
        await self.sync_sources()
        self.client.add_event_handlers(
            on_new=self._on_new,
            on_edit=self._on_edit,
            on_delete=self._on_delete,
        )
        for source in self.sources:
            async for message in self.client.iter_messages(str(source["chat_id"]), limit=self.history_limit):
                await self._ingest_message(message, source_id=str(source["source_id"]))
        if self.resync_seconds > 0:
            self._resync_task = asyncio.create_task(self._resync_loop())

    async def stop(self) -> None:
        if self._resync_task is not None:
            self._resync_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._resync_task
            self._resync_task = None
        await self.client.disconnect()

    async def _resync_loop(self) -> None:
        while True:
            await asyncio.sleep(self.resync_seconds)
            await self.sync_sources()

    async def sync_sources(self) -> list[dict[str, object]]:
        filters = [self._filter_mapping(item) async for item in self.client.iter_dialog_filters()]
        dialogs = [self._dialog_mapping(item) async for item in self.client.iter_dialogs()]
        self.sources = resolve_folder_chats(self.folder_name, filters=filters, dialogs=dialogs)
        if self.source_registry is not None:
            self.source_registry.sync(self.sources)
        return self.sources

    async def _on_new(self, event: Any) -> None:
        await self._ingest_message(event.message, source_id=self._source_id(event.message))

    async def _on_edit(self, event: Any) -> None:
        await self._ingest_message(event.message, source_id=self._source_id(event.message), edited=True)

    async def _on_delete(self, event: Any) -> None:
        chat_id = str(getattr(event, "chat_id", ""))
        received_at = datetime.now(UTC)
        for message_id in getattr(event, "deleted_ids", ()):
            self.collector.delete(
                chat_id=chat_id,
                message_id=int(message_id),
                received_at=received_at,
                source_id=self._source_id(event),
            )

    async def _ingest_message(self, message: Any, *, source_id: str, edited: bool = False) -> None:
        posted_at = getattr(message, "date", None) or datetime.now(UTC)
        received_at = datetime.now(UTC)
        media_path = None
        media_hash = None
        if getattr(message, "media", None) is not None and self.media_store is not None:
            download = getattr(self.client, "download_media", None)
            if download is not None:
                payload = await download(message)
                if payload:
                    artifact = self.media_store.put(payload, suffix=".bin")
                    media_path = str(artifact.path)
                    media_hash = artifact.media_hash
        self.collector.ingest(
            source_id=source_id,
            chat_id=str(getattr(message, "chat_id", "")),
            message_id=int(message.id),
            posted_at=posted_at,
            received_at=received_at,
            text=str(getattr(message, "message", "") or getattr(message, "text", "") or ""),
            caption=str(getattr(message, "caption", "") or ""),
            media_path=media_path,
            media_hash=media_hash,
            reply_to_message_id=getattr(getattr(message, "reply_to", None), "reply_to_msg_id", None),
            revision=None if not edited else None,
        )

    def _source_id(self, message: Any) -> str:
        chat_id = str(getattr(message, "chat_id", ""))
        for source in self.sources:
            if str(source.get("chat_id")) == chat_id:
                return str(source.get("source_id") or chat_id)
        return chat_id

    @staticmethod
    def _filter_mapping(item: Any) -> dict[str, object]:
        title = getattr(item, "title", None)
        title = getattr(title, "text", title)
        peers = getattr(item, "include_peers", None) or getattr(item, "pinned_peers", None) or ()
        chat_ids = []
        for peer in peers:
            try:
                from telethon.utils import get_peer_id

                chat_ids.append(str(get_peer_id(peer)))
                continue
            except Exception:  # noqa: BLE001
                pass
            for attribute in ("channel_id", "chat_id", "user_id", "id"):
                value = getattr(peer, attribute, None)
                if value is not None:
                    chat_ids.append(str(value))
                    break
            else:
                chat_ids.append(str(peer))
        return {"title": str(title or ""), "chat_ids": chat_ids}

    @staticmethod
    def _dialog_mapping(item: Any) -> dict[str, object]:
        entity = getattr(item, "entity", item)
        chat_id = getattr(item, "id", None) or getattr(entity, "id", None)
        title = getattr(item, "name", None) or getattr(entity, "title", None) or getattr(entity, "first_name", None)
        return {"chat_id": str(chat_id), "title": str(title or chat_id), "chat_type": type(entity).__name__}
