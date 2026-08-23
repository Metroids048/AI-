from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from .inbox import CandidateInbox


class TelegramV2Dispatcher:
    """Dispatches inbox payloads only after the existing writer authority is held.

    ``submit`` is an injected V2-cycle callback, never a Binance adapter.  The
    production integration binds it to the existing scheduler/cycle service.
    """

    def __init__(
        self,
        *,
        inbox: CandidateInbox,
        writer_authority: Callable[[], bool],
        submit: Callable[[dict], None],
    ) -> None:
        self.inbox = inbox
        self.writer_authority = writer_authority
        self.submit = submit

    def dispatch_once(self) -> dict[str, int]:
        if not self.writer_authority():
            return {"dispatched": 0, "blocked": len(self.inbox.pending())}
        dispatched = 0
        blocked = 0
        for item in list(self.inbox.pending()):
            if item.symbol not in {"BTC/USDT", "ETH/USDT"}:
                self.inbox.mark_blocked(item.candidate_key, reason="SYMBOL_NOT_EXECUTION_ELIGIBLE")
                blocked += 1
                continue
            self.submit(item.payload)
            self.inbox.mark_dispatched(item.candidate_key, dispatched_at=datetime.now(UTC))
            dispatched += 1
        return {"dispatched": dispatched, "blocked": blocked}
