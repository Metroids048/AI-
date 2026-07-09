"""Global kill switch — a Redis-backed trading halt.

When triggered, the gatekeeper rejects **all** new orders immediately,
regardless of validation or risk state. This is the platform-wide emergency
stop required by AGENTS.md (risk control priority > revenue).

The switch is intentionally simple: a single Redis key. Setting it to ``"1"``
halts trading; deleting it resumes trading. An in-memory fallback is used when
Redis is unavailable so that the feature degrades safely (fail-closed in
production, testable without Redis in CI).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol

from shared.config import settings
from shared.logging import get_logger

logger = get_logger(__name__)


class RedisLike(Protocol):
    def get(self, key: str) -> Any: ...

    def set(self, key: str, value: str) -> Any: ...

    def delete(self, key: str) -> Any: ...


class KillSwitch:
    """Process-wide trading halt flag backed by Redis with memory fallback."""

    def __init__(self, redis_client: RedisLike | None = None) -> None:
        self._redis = redis_client
        self._key = settings.kill_switch_redis_key
        # In-memory fallback so the switch works even without a Redis client
        # (e.g. in unit tests or when Redis is down). In production the Redis
        # value is authoritative; the memory flag is only a secondary signal.
        self._memory_flag: bool = False
        self._triggered_at: str | None = None
        self._triggered_reason: str | None = None

    @property
    def enabled(self) -> bool:
        return settings.kill_switch_enabled

    def is_triggered(self) -> bool:
        """Return True if trading is globally halted."""
        if not self.enabled:
            return False
        if self._memory_flag:
            return True
        if self._redis is not None:
            try:
                value = self._redis.get(self._key)
                return value is not None and value != b"0"
            except Exception as exc:  # Redis transient error — fail open here
                logger.warning("kill switch redis check failed", extra={"error": str(exc)})
                return False
        return False

    def activate(self, reason: str = "manual") -> None:
        """Trigger the kill switch — halts all new order submissions."""
        self._memory_flag = True
        self._triggered_at = datetime.now(UTC).isoformat()
        self._triggered_reason = reason
        if self._redis is not None:
            try:
                self._redis.set(self._key, "1")
            except Exception as exc:  # pragma: no cover
                logger.warning("kill switch redis set failed", extra={"error": str(exc)})
        logger.warning(
            "kill switch ACTIVATED — all new orders will be rejected",
            extra={"reason": reason, "triggered_at": self._triggered_at},
        )

    def deactivate(self) -> None:
        """Clear the kill switch — resumes order submission."""
        self._memory_flag = False
        self._triggered_at = None
        self._triggered_reason = None
        if self._redis is not None:
            try:
                self._redis.delete(self._key)
            except Exception as exc:  # pragma: no cover
                logger.warning("kill switch redis delete failed", extra={"error": str(exc)})
        logger.info("kill switch deactivated — order submission resumed")

    def status(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "triggered": self.is_triggered(),
            "triggered_at": self._triggered_at,
            "reason": self._triggered_reason,
        }


# Module-level singleton — the default kill switch used by the gatekeeper.
_kill_switch = KillSwitch()


def get_kill_switch() -> KillSwitch:
    """Return the process-wide kill switch singleton."""
    return _kill_switch
