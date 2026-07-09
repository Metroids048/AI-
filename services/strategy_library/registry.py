"""Strategy registry — registration, discovery, and hot-reload of runnable
strategy implementations.

The registry decouples strategy *definitions* (stored in the Strategy table)
from their *executable implementations*. A strategy handler is a callable that
generates signals from market data given a parameter set. Handlers are keyed
by ``strategy_key`` so they can be looked up at runtime by the paper/live
engines.

Hot-reload: re-registering a key replaces the handler in place — no process
restart required. Parameters can be updated independently via
``update_params()`` so that tuning does not require re-registering the whole
handler.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from shared.logging import get_logger

logger = get_logger(__name__)


@runtime_checkable
class StrategyHandler(Protocol):
    """Contract for a runnable strategy implementation."""

    def generate_signals(self, bars: list[dict], params: dict[str, Any]) -> list[dict]:
        """Produce entry/exit signals from a window of OHLCV bars."""
        ...

    def compute_stoploss(self, entry: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
        """Compute the stop-loss plan for a given entry."""
        ...

    def compute_takeprofit(self, entry: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
        """Compute the take-profit plan for a given entry."""
        ...


class StrategyRegistry:
    """In-process registry of strategy handlers keyed by ``strategy_key``."""

    def __init__(self) -> None:
        self._handlers: dict[str, StrategyHandler] = {}
        self._params: dict[str, dict[str, Any]] = {}
        self._versions: dict[str, int] = {}

    def register(
        self,
        strategy_key: str,
        handler: StrategyHandler,
        params: dict[str, Any] | None = None,
    ) -> None:
        """Register or hot-reload a strategy handler.

        Re-registering an existing key increments its version counter so
        callers can detect that the handler was swapped.
        """
        version = self._versions.get(strategy_key, 0) + 1
        self._handlers[strategy_key] = handler
        if params is not None:
            self._params[strategy_key] = dict(params)
        self._versions[strategy_key] = version
        logger.info(
            "strategy handler registered",
            extra={"strategy_key": strategy_key, "version": version},
        )

    def unregister(self, strategy_key: str) -> None:
        self._handlers.pop(strategy_key, None)
        self._params.pop(strategy_key, None)
        self._versions.pop(strategy_key, None)

    def get(self, strategy_key: str) -> StrategyHandler | None:
        return self._handlers.get(strategy_key)

    def get_params(self, strategy_key: str) -> dict[str, Any]:
        return dict(self._params.get(strategy_key, {}))

    def update_params(self, strategy_key: str, params: dict[str, Any]) -> bool:
        """Hot-update parameters without re-registering the handler.

        Returns ``True`` if the strategy was found and updated, ``False`` otherwise.
        """
        if strategy_key not in self._handlers:
            return False
        merged = dict(self._params.get(strategy_key, {}))
        merged.update(params)
        self._params[strategy_key] = merged
        logger.info(
            "strategy params updated",
            extra={"strategy_key": strategy_key, "keys": list(params.keys())},
        )
        return True

    def get_version(self, strategy_key: str) -> int:
        return self._versions.get(strategy_key, 0)

    def list_registered(self) -> list[str]:
        return list(self._handlers.keys())

    def is_registered(self, strategy_key: str) -> bool:
        return strategy_key in self._handlers


# Module-level singleton — the default registry used by the platform.
_registry = StrategyRegistry()


def get_strategy_registry() -> StrategyRegistry:
    """Return the process-wide strategy registry singleton."""
    return _registry
