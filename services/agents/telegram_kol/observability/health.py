from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class TelegramKolHealth:
    account_connected: bool = False
    session_valid: bool = False
    folder_found: bool = False
    source_count: int = 0
    last_message_at: datetime | None = None
    collector_latency_seconds: float | None = None
    parse_success: int = 0
    parse_ambiguous: int = 0
    shadow_signals: int = 0
    testnet_forward_sources: int = 0
    blocked_reasons: tuple[str, ...] = ()

    @property
    def status(self) -> str:
        return "HEALTHY" if self.account_connected and self.session_valid and self.folder_found else "DEGRADED"
