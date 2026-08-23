from __future__ import annotations

from collections.abc import Iterable, Mapping


def resolve_folder_chats(
    folder_name: str,
    *,
    filters: Iterable[Mapping[str, object]],
    dialogs: Iterable[Mapping[str, object]],
) -> list[dict[str, object]]:
    chat_ids: list[str] = []
    for item in filters:
        title = str(item.get("title") or item.get("name") or "")
        if title != folder_name:
            continue
        values = item.get("chat_ids") or item.get("include_peers") or ()
        if isinstance(values, (str, bytes)) or not isinstance(values, Iterable):
            continue
        for value in values:
            normalized = str(value)
            if normalized not in chat_ids:
                chat_ids.append(normalized)
    by_id = {str(dialog.get("chat_id")): dict(dialog) for dialog in dialogs}
    resolved: list[dict[str, object]] = []
    for chat_id in chat_ids:
        dialog = by_id.get(chat_id)
        if dialog is None:
            continue
        dialog.setdefault("source_id", chat_id)
        resolved.append(dialog)
    return resolved
