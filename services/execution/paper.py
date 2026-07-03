"""Paper-run orchestration helpers for the BTC/ETH-first simulated lane."""

from __future__ import annotations

from shared.models import PaperRun

PAPER_PRIORITY_SYMBOLS = ["BTC/USDT", "ETH/USDT"]


class PaperOrchestrationService:
    """Normalize paper-run candidates so the first symbols are always BTC/ETH."""

    def prepare_run(self, run: PaperRun) -> PaperRun:
        combined = list(dict.fromkeys([*PAPER_PRIORITY_SYMBOLS, *run.candidate_symbols, *run.symbol_scope]))
        symbol_scope = PAPER_PRIORITY_SYMBOLS if not run.symbol_scope else run.symbol_scope
        return run.model_copy(
            update={
                "symbol_scope": symbol_scope,
                "candidate_symbols": combined,
                "selection_basis": run.selection_basis or "binance_top20_quote_volume",
            }
        )
