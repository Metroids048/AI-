from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Protocol

from .ingestion.folder_resolver import resolve_folder_chats

READ_ONLY_VERIFY_OK = "READ_ONLY_VERIFY_OK"
TELEGRAM_AUTH_REQUIRED = "TELEGRAM_AUTH_REQUIRED"
TELEGRAM_FOLDER_NOT_FOUND = "TELEGRAM_FOLDER_NOT_FOUND"
TELEGRAM_FOLDER_EMPTY = "TELEGRAM_FOLDER_EMPTY"


class TelegramAuthBootstrapClient(Protocol):
    async def start_interactive(self) -> None: ...

    async def disconnect(self) -> None: ...

    async def is_user_authorized(self) -> bool: ...

    def iter_dialog_filters(self) -> AsyncIterator[Any]: ...

    def iter_dialogs(self) -> AsyncIterator[Any]: ...


@dataclass(frozen=True)
class TelegramBootstrapResult:
    status: str
    folder_name: str
    source_count: int = 0
    sources: tuple[dict[str, Any], ...] = ()
    available_folders: tuple[str, ...] = ()


def _filter_title(item: Any) -> str:
    title = getattr(item, "title", None)
    title = getattr(title, "text", title)
    return str(title or "")


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


def _filter_mapping(item: Any) -> dict[str, Any]:
    include_peers = tuple(getattr(item, "include_peers", None) or ())
    pinned_peers = tuple(getattr(item, "pinned_peers", None) or ())
    return {
        "title": _filter_title(item),
        "chat_ids": [_peer_id(peer) for peer in (*include_peers, *pinned_peers)],
    }


def _dialog_mapping(item: Any) -> dict[str, Any]:
    entity = getattr(item, "entity", item)
    chat_id = getattr(item, "id", None) or getattr(entity, "id", None)
    title = getattr(item, "name", None) or getattr(entity, "title", None) or getattr(entity, "first_name", None)
    return {
        "chat_id": str(chat_id),
        "title": str(title or chat_id),
        "chat_type": type(entity).__name__,
    }


async def authorize_and_verify(
    *,
    client: TelegramAuthBootstrapClient,
    folder_name: str,
) -> TelegramBootstrapResult:
    """Authorize one Telegram user session and verify folder visibility without trading or messaging."""

    try:
        await client.start_interactive()
        if not await client.is_user_authorized():
            return TelegramBootstrapResult(status=TELEGRAM_AUTH_REQUIRED, folder_name=folder_name)

        filters = [item async for item in client.iter_dialog_filters()]
        available_folders = tuple(title for title in (_filter_title(item) for item in filters) if title)
        if folder_name not in available_folders:
            return TelegramBootstrapResult(
                status=TELEGRAM_FOLDER_NOT_FOUND,
                folder_name=folder_name,
                available_folders=available_folders,
            )

        dialogs = [_dialog_mapping(item) async for item in client.iter_dialogs()]
        sources = resolve_folder_chats(
            folder_name,
            filters=[_filter_mapping(item) for item in filters],
            dialogs=dialogs,
        )
        if not sources:
            return TelegramBootstrapResult(
                status=TELEGRAM_FOLDER_EMPTY,
                folder_name=folder_name,
                available_folders=available_folders,
            )

        return TelegramBootstrapResult(
            status=READ_ONLY_VERIFY_OK,
            folder_name=folder_name,
            source_count=len(sources),
            sources=tuple(sources),
            available_folders=available_folders,
        )
    finally:
        await client.disconnect()
