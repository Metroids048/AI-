from datetime import UTC, datetime
from types import SimpleNamespace

from scripts.prove_real_binance_natural_order import _eligible_order


def _order(*, created_at: datetime) -> SimpleNamespace:
    return SimpleNamespace(
        created_at=created_at,
        paper_run_id="run-1",
        symbol="BTC/USDT",
        close_only_mode=False,
        gateway_order_id="12345",
        gateway_name="binance_usdt_perpetual",
        order_origin="PAPER_SCHEDULER",
        test_run_id=None,
        entry_context={"paper_order_should_trade": True, "decision_variant": "primary"},
    )


def test_eligible_order_accepts_sqlite_naive_utc_created_at() -> None:
    order = _order(created_at=datetime(2026, 7, 24, 9, 45))

    assert _eligible_order(
        order,
        run_ids={"run-1"},
        since=datetime(2026, 7, 24, 9, 42, tzinfo=UTC),
    )
