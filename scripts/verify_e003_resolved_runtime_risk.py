"""Read-only E-003 resolved runtime risk inspection for the active directional lane.

Prints the final resolved risk envelope per symbol from the authoritative
PaperRun/ConfigSnapshot pair the V2 scheduler actually consumes. Never prints
secrets and never writes to the database.
"""

from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from services.automated_trading.application.operator_profile import resolve_v2_execution_settings
from services.database import get_session_factory
from services.execution.risk_tiers import SYMBOL_RISK_BASE_CEILINGS
from services.strategy_library import ConfigSnapshotRepository, PaperRunRepository

SYMBOLS = ("BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT", "XRP/USDT")
LEGACY_RISK_PER_TRADE = Decimal("0.01")
LEGACY_MAX_LEVERAGE = 50
LEGACY_POSITION_FRACTION = Decimal("2.50")


def inspect_resolved_runtime_risk() -> dict[str, object]:
    with get_session_factory()() as session:
        paper_repo = PaperRunRepository(session)
        config_repo = ConfigSnapshotRepository(session)
        running = [
            run
            for run in paper_repo.list_paper_runs()
            if run.paper_status == "running" and run.execution_profile.get("strategy_lane") == "directional"
        ]
        if not running:
            return {"status": "E003_RUNTIME_NOT_EFFECTIVE", "reason": "no running directional PaperRun"}
        run = running[0]
        active = config_repo.get_active(run.paper_run_id or "")
        pending = config_repo.get_pending(run.paper_run_id or "")
        active_profile = active.config.get("execution_profile") if active is not None else None
        profile = active_profile if isinstance(active_profile, dict) else run.execution_profile

        rows: list[dict[str, object]] = []
        still_permissive: list[str] = []
        for symbol in SYMBOLS:
            settings = resolve_v2_execution_settings(symbol, profile)
            bounds = SYMBOL_RISK_BASE_CEILINGS.get(symbol, {})
            within_bounds = (
                bool(bounds)
                and settings.risk_per_trade <= Decimal(str(bounds["risk_per_trade"]))
                and settings.max_leverage <= bounds["max_leverage"]
                and settings.max_margin_fraction <= Decimal(str(bounds["max_margin_fraction"]))
            )
            if (
                settings.risk_per_trade >= LEGACY_RISK_PER_TRADE
                or settings.max_leverage >= LEGACY_MAX_LEVERAGE
                or settings.max_position_fraction >= LEGACY_POSITION_FRACTION
            ):
                still_permissive.append(symbol)
            rows.append(
                {
                    "symbol": symbol,
                    "resolved_risk_per_trade": str(settings.risk_per_trade),
                    "resolved_max_leverage": settings.max_leverage,
                    "resolved_max_margin_fraction": str(settings.max_margin_fraction),
                    "resolved_max_position_fraction": str(settings.max_position_fraction),
                    "volatility_multiplier": str(settings.volatility_multiplier),
                    "volatility_no_new_entry": settings.volatility_no_new_entry,
                    "within_authorized_bounds": within_bounds,
                }
            )

        return {
            "status": "E003_RUNTIME_EFFECTIVE" if not still_permissive else "E003_RUNTIME_NOT_EFFECTIVE",
            "paper_run_id": run.paper_run_id,
            "active_config_snapshot_id": active.config_snapshot_id if active else None,
            "active_config_hash": run.active_config_hash,
            "active_created_by": active.created_by if active else None,
            "pending_config_snapshot_id": pending.config_snapshot_id if pending else None,
            "pending_created_by": pending.created_by if pending else None,
            "symbols_still_permissive": still_permissive,
            "asset_risk_tier_names": sorted((profile.get("asset_risk_tiers") or {}).keys()),
            "volatility_risk_tier_names": sorted((profile.get("volatility_risk_tiers") or {}).keys()),
            "resolved": rows,
        }


if __name__ == "__main__":
    print(json.dumps(inspect_resolved_runtime_risk(), ensure_ascii=False, indent=2))
