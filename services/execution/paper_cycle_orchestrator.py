"""Stable orchestration boundary for autonomous paper-runtime cycles."""

from __future__ import annotations

from collections.abc import Callable

from shared.models import PaperRuntimeCycleRequest, PaperRuntimeCycleResult


class PaperCycleOrchestrator:
    """Own the public cycle hand-off while preserving the runtime contract."""

    def __init__(
        self,
        *,
        cycle_runner: Callable[[str, PaperRuntimeCycleRequest], PaperRuntimeCycleResult],
    ) -> None:
        self._cycle_runner = cycle_runner

    def run_cycle(
        self,
        *,
        paper_run_id: str,
        request: PaperRuntimeCycleRequest,
    ) -> PaperRuntimeCycleResult:
        return self._cycle_runner(paper_run_id, request)
