from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SourceProfile:
    source_id: str
    title: str
    aliases: dict[str, str] = field(default_factory=dict)
    parser_hints: tuple[str, ...] = ()
