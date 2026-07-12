"""Paper-run orchestration helpers for the BTC/ETH-first simulated lane."""

from __future__ import annotations

from services.data.service import DEFAULT_BINANCE_TOP20
from shared.models import PaperRun

PAPER_PRIORITY_SYMBOLS = ["BTC/USDT", "ETH/USDT"]


def _default_execution_profile(profile: dict) -> dict:
    merged = dict(profile)
    if "mirror_to_gateway" not in merged:
        merged["mirror_to_gateway"] = False
    return merged


class PaperOrchestrationService:
    """Normalize paper-run candidates so the first symbols are always BTC/ETH."""

    def prepare_run(self, run: PaperRun) -> PaperRun:
        default_candidates = run.candidate_symbols or DEFAULT_BINANCE_TOP20
        combined = list(dict.fromkeys([*PAPER_PRIORITY_SYMBOLS, *default_candidates, *run.symbol_scope]))
        symbol_scope = list(dict.fromkeys(run.symbol_scope or DEFAULT_BINANCE_TOP20))
        return run.model_copy(
            update={
                "symbol_scope": symbol_scope,
                "candidate_symbols": combined,
                "selection_basis": run.selection_basis or "fixed_operator_top20",
                "execution_profile": _default_execution_profile(run.execution_profile),
            }
        )
