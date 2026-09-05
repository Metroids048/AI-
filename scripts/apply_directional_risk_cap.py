"""Stage the operator-approved directional 50x / 5%-margin sizing profile.

This updates only local configuration and the NEXT_CYCLE snapshot.  It never
calls Binance, changes leverage on an existing position, closes a position, or
touches protection orders.
"""

from __future__ import annotations

import argparse
import copy
import sys

from services.execution.risk_tiers import scale_asset_risk_tiers

TARGET_LEVERAGE = 50.0
TARGET_MARGIN_FRACTION = 0.05
TARGET_POSITION_FRACTION = TARGET_LEVERAGE * TARGET_MARGIN_FRACTION
TARGET_RISK_PER_TRADE = 0.01


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    from services.database import get_session_factory
    from services.strategy_library import (
        ConfigSnapshotRepository,
        PaperRunRepository,
        RiskProfileRepository,
        StrategyRepository,
    )
    from shared.models import ConfigSnapshot, StrategyUpdate
    from shared.models.risk import RiskProfileUpdate

    with get_session_factory()() as session:
        paper_repo = PaperRunRepository(session)
        candidates = [
            run
            for run in paper_repo.list_paper_runs()
            if run.paper_status == "running"
            and run.execution_profile.get("strategy_lane") == "directional"
            and (args.run_id is None or run.paper_run_id == args.run_id)
        ]
        if len(candidates) != 1:
            print(f"expected exactly one running directional PaperRun, found {len(candidates)}", file=sys.stderr)
            return 1

        run = candidates[0]
        assert run.paper_run_id is not None
        current = copy.deepcopy(run.execution_profile)
        updated = copy.deepcopy(current)
        updated["risk_per_trade"] = TARGET_RISK_PER_TRADE
        updated["max_leverage"] = TARGET_LEVERAGE
        updated["max_symbol_exposure"] = TARGET_POSITION_FRACTION
        updated["max_margin_fraction"] = TARGET_MARGIN_FRACTION
        updated["asset_risk_tiers"] = scale_asset_risk_tiers(
            current.get("asset_risk_tiers"),
            max_leverage=TARGET_LEVERAGE,
            max_symbol_exposure=TARGET_POSITION_FRACTION,
        )

        strategy_repo = StrategyRepository(session)
        strategy = strategy_repo.get_strategy(run.strategy_id)
        strategy_rules = strategy.rules.model_dump(mode="json") if strategy is not None else {}
        if strategy is not None:
            position_rules = {
                **strategy.rules.position_rules,
                "risk_per_trade": TARGET_RISK_PER_TRADE,
                "max_leverage": TARGET_LEVERAGE,
                "max_position_fraction": TARGET_POSITION_FRACTION,
            }
            updated_strategy = strategy.rules.model_copy(update={"position_rules": position_rules})
            strategy_repo.update_strategy(run.strategy_id, StrategyUpdate(rules=updated_strategy))
            strategy_rules = updated_strategy.model_dump(mode="json")

        risk_profile_id = str(current.get("risk_profile_id") or "")
        if risk_profile_id:
            RiskProfileRepository(session).update_profile(
                risk_profile_id,
                RiskProfileUpdate(
                    single_trade_risk_limit=TARGET_RISK_PER_TRADE,
                    max_leverage=TARGET_LEVERAGE,
                    max_symbol_exposure=TARGET_POSITION_FRACTION,
                ),
            )

        print(f"paper_run_id={run.paper_run_id}")
        print(
            f"before risk_per_trade={current.get('risk_per_trade')} max_leverage={current.get('max_leverage')} max_symbol_exposure={current.get('max_symbol_exposure')}"
        )
        print(
            f"after  risk_per_trade={updated['risk_per_trade']} max_leverage={updated['max_leverage']} max_symbol_exposure={updated['max_symbol_exposure']}"
        )
        if not args.apply:
            print("DRY RUN: no configuration written")
            session.rollback()
            return 0

        snapshots = ConfigSnapshotRepository(session)
        active = snapshots.get_active(run.paper_run_id)
        if snapshots.get_pending(run.paper_run_id) is not None:
            print("a pending ConfigSnapshot already exists; refusing to overwrite it", file=sys.stderr)
            session.rollback()
            return 1
        base_config = dict(active.config) if active is not None else {}
        config = {
            **base_config,
            "execution_profile": updated,
            "strategy_rules": strategy_rules,
            "risk_profile_id": risk_profile_id or None,
        }
        snapshot = ConfigSnapshot.create(
            paper_run_id=run.paper_run_id,
            config=config,
            created_by="directional-risk-cap-remediation",
            effective_cycle_id="NEXT_CYCLE",
            previous_snapshot_id=active.config_snapshot_id if active is not None else None,
        )
        staged = snapshots.stage_execution_profile_snapshot(
            snapshot,
            execution_profile=updated,
            base_config_hash=run.active_config_hash,
        )
        print(f"staged_snapshot={staged.config_snapshot_id}")
        print("applies on NEXT_CYCLE; exchange positions and protection orders were not touched")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
