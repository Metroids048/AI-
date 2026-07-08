"""Unified strategy runner interface for backtest, paper, and live execution.

Both the backtest engine and the paper/live runtime implement this interface so
that the orchestration layer (Celery tasks, API routers) can invoke them
polymorphically with a consistent parameter and result contract.

Key responsibilities:
  * Resolve parameters by merging ``StrategyRegistry`` defaults with caller
    overrides — so tuning does not require code changes.
  * Return a unified ``RunResult`` regardless of engine, so downstream
    consumers (status write-back, review layer) need no engine-specific code.
  * Provide the extension point for future engines (optimization, walk-forward).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from services.strategy_library.registry import get_strategy_registry


@dataclass
class RunRequest:
    """Engine-agnostic execution request."""

    strategy_id: str
    strategy_key: str
    run_type: str = "backtest"  # backtest | paper | live
    version_id: str | None = None
    params: dict[str, Any] | None = None  # overrides registry defaults
    symbol_scope: list[str] | None = None
    timeframe: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class RunResult:
    """Engine-agnostic execution result."""

    run_id: str
    run_type: str
    status: str  # completed | failed | running | rejected
    metrics: dict[str, Any] | None = None
    error: str | None = None
    lifecycle_status: str | None = None  # value to write back to Strategy table


class StrategyRunner(ABC):
    """Unified interface for strategy execution across validation/paper/live."""

    @abstractmethod
    def run(self, request: RunRequest) -> RunResult:
        """Execute the strategy and return a unified result.

        Implementations should:
          1. Resolve parameters via ``resolve_params``.
          2. Execute the engine-specific logic.
          3. Map the engine result to ``RunResult``.
        """
        ...

    @staticmethod
    def resolve_params(strategy_key: str, overrides: dict[str, Any] | None) -> dict[str, Any]:
        """Merge registry defaults with caller overrides.

        Registry defaults come from ``StrategyRegistry.get_params``; caller
        overrides take precedence so per-run tuning is possible without
        mutating the shared registry state.
        """
        registry = get_strategy_registry()
        params = registry.get_params(strategy_key)
        if overrides:
            params.update(overrides)
        return params


class BacktestRunner(StrategyRunner):
    """Adapter that delegates to the carry backtest application service.

    This is a thin adapter — the heavy lifting stays in
    ``CarryBacktestApplicationService``. The adapter's value is giving the
    orchestration layer a single ``StrategyRunner`` interface to call.
    """

    def __init__(self, app_service: Any) -> None:
        self._app_service = app_service

    def run(self, request: RunRequest) -> RunResult:
        raise NotImplementedError(
            "BacktestRunner.run is delegated to CarryBacktestApplicationService; "
            "use the application service directly for the carry lane. This adapter "
            "exists to unify the interface contract for future generic backtest "
            "engines (vectorbt/backtrader) and the optimization worker."
        )


class PaperRunner(StrategyRunner):
    """Adapter that delegates to PaperRuntimeService."""

    def __init__(self, runtime_service: Any) -> None:
        self._runtime_service = runtime_service

    def run(self, request: RunRequest) -> RunResult:
        raise NotImplementedError(
            "PaperRunner.run is delegated to PaperRuntimeService.run_cycle; "
            "use the runtime service directly. This adapter exists to unify the "
            "interface contract for future orchestration."
        )
