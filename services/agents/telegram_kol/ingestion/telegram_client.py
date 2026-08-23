from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any, Protocol


class TelegramClientPort(Protocol):
    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    async def is_user_authorized(self) -> bool: ...

    def iter_dialog_filters(self) -> AsyncIterator[Any]: ...

    def iter_dialogs(self) -> AsyncIterator[Any]: ...

    def iter_messages(self, chat_id: str, *, limit: int) -> AsyncIterator[Any]: ...

    async def download_media(self, message: Any) -> bytes | None: ...

    def add_event_handlers(
        self,
        *,
        on_new: Callable[[Any], Awaitable[None]],
        on_edit: Callable[[Any], Awaitable[None]],
        on_delete: Callable[[Any], Awaitable[None]],
    ) -> None: ...


class TelegramAuthRequired(RuntimeError):
    """The operator must complete official Telegram authentication locally."""


class TelethonTelegramClient:
    """Thin optional Telethon adapter; no outbound Telegram actions are exposed."""

    def __init__(self, *, api_id: int, api_hash: str, phone: str, session_dir: str | Path) -> None:
        self.api_id = api_id
        self.api_hash = api_hash
        self.phone = phone
        self.session_dir = Path(session_dir)
        self.session_dir.mkdir(parents=True, exist_ok=True)
        try:
            from telethon import TelegramClient
        except ImportError as exc:  # pragma: no cover - depends on optional runtime dependency
            raise RuntimeError("Telethon is required for the Telegram User API collector") from exc
        self._client = TelegramClient(str(self.session_dir / "telegram_kol"), api_id, api_hash)

    async def connect(self) -> None:
        await self._client.connect()
        if not await self._client.is_user_authorized():
            raise TelegramAuthRequired("TELEGRAM_AUTH_REQUIRED: complete Telegram verification locally")

    async def disconnect(self) -> None:
        await self._client.disconnect()

    async def is_user_authorized(self) -> bool:
        return bool(await self._client.is_user_authorized())

    async def iter_dialog_filters(self) -> AsyncIterator[Any]:
        from telethon.tl.functions.messages import GetDialogFiltersRequest

        filters = await self._client(GetDialogFiltersRequest())
        for item in filters.filters:
            yield item

    async def iter_dialogs(self) -> AsyncIterator[Any]:
        async for dialog in self._client.iter_dialogs():
            yield dialog

    async def iter_messages(self, chat_id: str, *, limit: int) -> AsyncIterator[Any]:
        async for message in self._client.iter_messages(chat_id, limit=limit):
            yield message

    async def download_media(self, message: Any) -> bytes | None:
        payload = await self._client.download_media(message, file=bytes)
        return payload if isinstance(payload, bytes) else None

    def add_event_handlers(
        self,
        *,
        on_new: Callable[[Any], Awaitable[None]],
        on_edit: Callable[[Any], Awaitable[None]],
        on_delete: Callable[[Any], Awaitable[None]],
    ) -> None:
        from telethon import events

        self._client.add_event_handler(on_new, events.NewMessage())
        self._client.add_event_handler(on_edit, events.MessageEdited())
        self._client.add_event_handler(on_delete, events.MessageDeleted())
