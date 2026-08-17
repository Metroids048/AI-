"""Read-only: print the live operator sizing profile exactly as the cycle sees it.

Resolves the profile through the same path the scheduler uses
(_load_v2_operator_execution_settings -> resolve_v2_execution_settings) so the
printed values are what a cycle would actually receive, not a guess.
"""

from __future__ import annotations

from decimal import Decimal


def main() -> int:
    from services.automated_trading.application.operator_profile import (
        resolve_v2_execution_settings,
    )
    from services.database import get_session_factory
    from services.strategy_library import ConfigSnapshotRepository, PaperRunRepository

    with get_session_factory()() as session:
        paper_repo = PaperRunRepository(session)
        running = [
            run
            for run in paper_repo.list_paper_runs()
            if run.paper_status == "running" and run.execution_profile.get("strategy_lane") == "directional"
        ]
        if len(running) != 1:
            print(f"expected exactly 1 running directional PaperRun, found {len(running)}")
            return 1

        run = running[0]
        if run.paper_run_id is None:
            print("directional PaperRun has no id")
            return 1
        profile = run.execution_profile
        snapshots = ConfigSnapshotRepository(session)
        active = snapshots.get_active(run.paper_run_id)
        pending = snapshots.get_pending(run.paper_run_id)

        print(f"paper_run_id        : {run.paper_run_id}")
        print(f"active_snapshot     : {getattr(active, 'config_snapshot_id', None)}")
        print(f"pending_snapshot    : {getattr(pending, 'config_snapshot_id', None)}")
        print()
        print("PaperRun.execution_profile (profile-wide):")
        for key in (
            "risk_per_trade",
            "max_leverage",
            "max_margin_fraction",
            "max_symbol_exposure",
            "max_total_exposure",
            "order_notional_usdt",
        ):
            print(f"  {key:22}= {profile.get(key, '<absent>')}")

        tiers = profile.get("asset_risk_tiers") or {}
        print("\nasset_risk_tiers:")
        for name, tier in tiers.items():
            if isinstance(tier, dict):
                print(
                    f"  {name:10} leverage={tier.get('leverage')} "
                    f"max_position_fraction={tier.get('max_position_fraction')} "
                    f"symbols={tier.get('symbols')}"
                )

        print("\nRESOLVED per symbol (what a cycle receives):")
        for symbol in ("BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "BNB/USDT"):
            settings = resolve_v2_execution_settings(symbol, profile)
            equity = Decimal("7349")
            notional = equity * settings.max_position_fraction
            print(
                f"  {symbol}: leverage={settings.max_leverage}x "
                f"margin_fraction={settings.max_margin_fraction} "
                f"exposure={settings.max_position_fraction} "
                f"risk_per_trade={settings.risk_per_trade} "
                f"-> notional@{equity}={notional:.2f} "
                f"margin={notional / settings.max_leverage:.2f}"
            )

        if pending is not None:
            pending_profile = (pending.config or {}).get("execution_profile") or {}
            print("\nPENDING snapshot would change resolved values to:")
            for symbol in ("BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "BNB/USDT"):
                settings = resolve_v2_execution_settings(symbol, pending_profile)
                print(f"  {symbol}: leverage={settings.max_leverage}x exposure={settings.max_position_fraction}")
        else:
            print("\nNo pending snapshot: next cycle keeps the values above.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
