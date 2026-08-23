from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest

from services.agents.telegram_kol.auth_bootstrap import (
    READ_ONLY_VERIFY_OK,
    TELEGRAM_AUTH_REQUIRED,
    TELEGRAM_FOLDER_NOT_FOUND,
    authorize_and_verify,
)


@dataclass
class _Title:
    text: str


@dataclass
class _Filter:
    title: _Title
    include_peers: tuple[Any, ...]
    pinned_peers: tuple[Any, ...] = ()


@dataclass
class _Peer:
    id: int


@dataclass
class _Entity:
    id: int
    title: str


@dataclass
class _Dialog:
    id: int
    name: str
    entity: _Entity


class FakeClient:
    def __init__(
        self,
        *,
        authorized: bool = True,
        filters: tuple[Any, ...] = (),
        dialogs: tuple[Any, ...] = (),
    ) -> None:
        self.authorized = authorized
        self.filters = filters
        self.dialogs = dialogs
        self.started = False
        self.disconnected = False

    async def start_interactive(self) -> None:
        self.started = True

    async def is_user_authorized(self) -> bool:
        return self.authorized

    async def disconnect(self) -> None:
        self.disconnected = True

    async def iter_dialog_filters(self) -> AsyncIterator[Any]:
        for item in self.filters:
            yield item

    async def iter_dialogs(self) -> AsyncIterator[Any]:
        for item in self.dialogs:
            yield item


@pytest.mark.asyncio
async def test_authorize_and_verify_returns_read_only_sources_and_disconnects() -> None:
    client = FakeClient(
        filters=(
            _Filter(
                title=_Title("搬运脚本分组"),
                include_peers=(_Peer(id=-1001234),),
                pinned_peers=(_Peer(id=-1005678),),
            ),
        ),
        dialogs=(
            _Dialog(
                id=-1001234,
                name="KOL signals",
                entity=_Entity(id=1234, title="KOL signals"),
            ),
            _Dialog(
                id=-1005678,
                name="Pinned signals",
                entity=_Entity(id=5678, title="Pinned signals"),
            ),
        ),
    )

    result = await authorize_and_verify(client=client, folder_name="搬运脚本分组")

    assert client.started is True
    assert client.disconnected is True
    assert result.status == READ_ONLY_VERIFY_OK
    assert result.folder_name == "搬运脚本分组"
    assert result.source_count == 2
    assert result.sources == (
        {
            "chat_id": "-1001234",
            "title": "KOL signals",
            "chat_type": "_Entity",
            "source_id": "-1001234",
        },
        {
            "chat_id": "-1005678",
            "title": "Pinned signals",
            "chat_type": "_Entity",
            "source_id": "-1005678",
        },
    )


@pytest.mark.asyncio
async def test_authorize_and_verify_reports_auth_required_without_reading_dialogs() -> None:
    client = FakeClient(authorized=False)

    result = await authorize_and_verify(client=client, folder_name="搬运脚本分组")

    assert result.status == TELEGRAM_AUTH_REQUIRED
    assert result.source_count == 0
    assert client.disconnected is True


@pytest.mark.asyncio
async def test_authorize_and_verify_distinguishes_missing_folder() -> None:
    client = FakeClient(
        filters=(_Filter(title=_Title("其他分组"), include_peers=()),),
        dialogs=(),
    )

    result = await authorize_and_verify(client=client, folder_name="搬运脚本分组")

    assert result.status == TELEGRAM_FOLDER_NOT_FOUND
    assert result.source_count == 0
    assert result.available_folders == ("其他分组",)
    assert client.disconnected is True
