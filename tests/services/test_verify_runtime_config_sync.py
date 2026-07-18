from __future__ import annotations

from scripts.verify_runtime_config_sync import _diff_strategy_rules, _load_live_strategy_rules
from services.execution.bootstrap import AUTO_PAPER_TECHNICAL_KEY, AUTO_PAPER_TECHNICAL_RULES
from shared.config import settings
from shared.models import StrategyUpdate


def test_diff_reports_no_mismatch_when_code_and_db_rules_are_identical() -> None:
    assert _diff_strategy_rules(AUTO_PAPER_TECHNICAL_RULES, AUTO_PAPER_TECHNICAL_RULES) == []


def test_diff_reports_mismatched_field_with_both_values() -> None:
    drifted = {
        **AUTO_PAPER_TECHNICAL_RULES,
        "position_rules": {**AUTO_PAPER_TECHNICAL_RULES["position_rules"], "risk_per_trade": 0.099},
    }

    mismatches = _diff_strategy_rules(AUTO_PAPER_TECHNICAL_RULES, drifted)

    assert len(mismatches) == 1
    mismatch = mismatches[0]
    assert mismatch.block == "position_rules"
    assert mismatch.field == "risk_per_trade"
    assert mismatch.code_value == AUTO_PAPER_TECHNICAL_RULES["position_rules"]["risk_per_trade"]
    assert mismatch.db_value == 0.099


def test_load_live_strategy_rules_detects_drift_without_a_restart(db_session, monkeypatch) -> None:
    """Scenario 1: a rules dict changes in code, but nothing re-bootstraps the
    Strategy row (the exact "stale process" failure mode Module 0 exists to catch)."""
    import services.execution.bootstrap as bootstrap_module
    from services.execution.bootstrap import bootstrap_auto_trading_technical_paper_run

    monkeypatch.setattr(settings, "binance_api_key", "key")
    monkeypatch.setattr(settings, "binance_api_secret", "secret")
    # Pin evidence resolution to the fallback path so the test is independent
    # of any local manifest artifact that may exist on the test machine.
    monkeypatch.setattr(
        bootstrap_module,
        "resolve_auto_paper_technical_evidence",
        lambda: (AUTO_PAPER_TECHNICAL_RULES, tuple(("BTC/USDT", "ETH/USDT", "SOL/USDT"))),
    )
    assert bootstrap_auto_trading_technical_paper_run() is not None

    database_url = str(db_session.get_bind().engine.url)
    live_rules = _load_live_strategy_rules(database_url, AUTO_PAPER_TECHNICAL_KEY)
    assert live_rules is not None
    assert _diff_strategy_rules(AUTO_PAPER_TECHNICAL_RULES, live_rules) == []

    # Simulate an edit to bootstrap.py that never gets synced back to the DB.
    from services.strategy_library import StrategyRepository

    repo = StrategyRepository(db_session)
    strategy = next(item for item in repo.list_strategies() if item.strategy_key == AUTO_PAPER_TECHNICAL_KEY)
    stale_rules = strategy.rules.model_copy(deep=True)
    stale_rules.position_rules["risk_per_trade"] = 0.099
    repo.update_strategy(strategy.strategy_id or "", StrategyUpdate(rules=stale_rules))
    db_session.commit()

    drifted_code_rules = {
        **AUTO_PAPER_TECHNICAL_RULES,
        "position_rules": {**AUTO_PAPER_TECHNICAL_RULES["position_rules"], "risk_per_trade": 0.099},
    }
    live_rules_after_edit = _load_live_strategy_rules(database_url, AUTO_PAPER_TECHNICAL_KEY)
    assert live_rules_after_edit is not None
    mismatches = _diff_strategy_rules(drifted_code_rules, live_rules_after_edit)
    assert mismatches == []  # DB was hand-edited to match the "new code", not re-bootstrapped

    # But the *actual* current code constant still disagrees with the DB -- this
    # is the real mismatch the tool must surface.
    real_mismatches = _diff_strategy_rules(AUTO_PAPER_TECHNICAL_RULES, live_rules_after_edit)
    assert len(real_mismatches) == 1
    assert real_mismatches[0].field == "risk_per_trade"


def test_load_live_strategy_rules_goes_green_after_proper_restart(db_session, monkeypatch) -> None:
    """Scenario 2: re-running bootstrap (what a proper restart does) re-syncs the
    Strategy row, so the diff goes back to empty."""
    import services.execution.bootstrap as bootstrap_module
    from services.execution.bootstrap import bootstrap_auto_trading_technical_paper_run

    monkeypatch.setattr(settings, "binance_api_key", "key")
    monkeypatch.setattr(settings, "binance_api_secret", "secret")
    monkeypatch.setattr(
        bootstrap_module,
        "resolve_auto_paper_technical_evidence",
        lambda: (AUTO_PAPER_TECHNICAL_RULES, tuple(("BTC/USDT", "ETH/USDT", "SOL/USDT"))),
    )
    assert bootstrap_auto_trading_technical_paper_run() is not None

    from services.strategy_library import StrategyRepository

    repo = StrategyRepository(db_session)
    strategy = next(item for item in repo.list_strategies() if item.strategy_key == AUTO_PAPER_TECHNICAL_KEY)
    stale_rules = strategy.rules.model_copy(deep=True)
    stale_rules.position_rules["risk_per_trade"] = 0.099
    repo.update_strategy(strategy.strategy_id or "", StrategyUpdate(rules=stale_rules))
    db_session.commit()

    database_url = str(db_session.get_bind().engine.url)
    live_rules_before_restart = _load_live_strategy_rules(database_url, AUTO_PAPER_TECHNICAL_KEY)
    assert live_rules_before_restart is not None
    assert len(_diff_strategy_rules(AUTO_PAPER_TECHNICAL_RULES, live_rules_before_restart)) == 1

    assert bootstrap_auto_trading_technical_paper_run() is not None  # the "restart"

    live_rules_after_restart = _load_live_strategy_rules(database_url, AUTO_PAPER_TECHNICAL_KEY)
    assert live_rules_after_restart is not None
    assert _diff_strategy_rules(AUTO_PAPER_TECHNICAL_RULES, live_rules_after_restart) == []
