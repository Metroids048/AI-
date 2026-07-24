from services.data.universe import FIXED_TOP20_ASSETS
from services.execution.bootstrap import refresh_fixed_top20_runtime_universe
from services.strategy_library import ConfigSnapshotRepository, PaperRunRepository, StrategyRepository
from shared.models import ConfigSnapshot, PaperRun, StrategyCreate


def _exchange_info_symbols() -> list[dict]:
    return [
        {
            "symbol": item["exchange_symbol"],
            "status": "TRADING",
            "pricePrecision": 2,
            "quantityPrecision": 3,
            "filters": [{"filterType": "MIN_NOTIONAL", "notional": "5"}],
        }
        for item in FIXED_TOP20_ASSETS
    ]


def test_exchange_info_refresh_stages_active_runtime_snapshot(monkeypatch, db_session) -> None:  # noqa: ANN001
    strategy = StrategyRepository(db_session).create_strategy(
        StrategyCreate(
            strategy_key="fixed-universe-test",
            source="test",
            core_thesis="test",
            rules={
                "entry_rules": {},
                "exit_rules": {},
                "stoploss_rules": {},
                "takeprofit_rules": {},
                "position_rules": {},
            },
        )
    )
    profile = {
        "universe_mode": "fixed_top20",
        "universe_assets": [
            {
                "platform_symbol": "BTC/USDT",
                "tradable_status": "unknown",
                "reason": "exchangeInfo unavailable",
            }
        ],
    }
    run = PaperRunRepository(db_session).create_paper_run(
        PaperRun(strategy_id=strategy.strategy_id or "", execution_profile=profile, paper_status="running")
    )
    ConfigSnapshotRepository(db_session).create_snapshot(
        ConfigSnapshot.create(
            paper_run_id=run.paper_run_id or "",
            config={"execution_profile": profile, "strategy_rules": strategy.rules.model_dump(mode="json")},
            created_by="test",
            effective_cycle_id="baseline",
        ),
        base_config_hash=None,
    )
    monkeypatch.setattr("services.database.get_session_factory", lambda: lambda: db_session)

    assert refresh_fixed_top20_runtime_universe(_exchange_info_symbols()) == 1

    config_repo = ConfigSnapshotRepository(db_session)
    pending = config_repo.get_pending(run.paper_run_id or "")
    assert pending is not None
    assets = pending.config["execution_profile"]["universe_assets"]
    assert next(asset for asset in assets if asset["platform_symbol"] == "BTC/USDT")["tradable_status"] == "trading"
