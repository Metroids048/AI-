"""Paper-run orchestration helpers for the BTC/ETH-first simulated lane."""

from __future__ import annotations

from services.data.service import DEFAULT_BINANCE_TOP20
from shared.config import settings
from shared.models import PaperRun

PAPER_PRIORITY_SYMBOLS = ["BTC/USDT", "ETH/USDT"]


def _default_execution_profile(profile: dict) -> dict:
    merged = dict(profile)
    if "mirror_to_gateway" not in merged and settings.binance_api_key and settings.binance_api_secret:
        merged["mirror_to_gateway"] = True
    return merged


class PaperOrchestrationService:
    """Normalize paper-run candidates so the first symbols are always BTC/ETH."""

    def prepare_run(self, run: PaperRun) -> PaperRun:
        default_candidates = run.candidate_symbols or DEFAULT_BINANCE_TOP20
        combined = list(dict.fromkeys([*PAPER_PRIORITY_SYMBOLS, *default_candidates, *run.symbol_scope]))
        symbol_scope = PAPER_PRIORITY_SYMBOLS if not run.symbol_scope else run.symbol_scope
        return run.model_copy(
            update={
                "symbol_scope": symbol_scope,
                "candidate_symbols": combined,
                "selection_basis": run.selection_basis or "binance_top20_quote_volume",
                "execution_profile": _default_execution_profile(run.execution_profile),
            }
        )
