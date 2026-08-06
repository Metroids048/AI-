"""Semantic parity checks for QuantDinger Shadow replay observations."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from services.validation.quantdinger_differential_replay import (
    DifferentialMismatchCode,
    QuantDingerReplayArtifactError,
    QuantDingerReplayTrade,
    compare_quantdinger_replay,
    parse_quantdinger_replay_artifact,
)
from services.validation.technical_replay import ReplayTrade
from shared.models import TradeSide


def _local_trade() -> ReplayTrade:
    opened_at = datetime(2026, 8, 5, 12, 15, tzinfo=UTC)
    return ReplayTrade(
        symbol="BTC/USDT",
        side=TradeSide.LONG,
        opened_at=opened_at,
        closed_at=opened_at + timedelta(hours=1),
        entry_price=Decimal("65000"),
        exit_price=Decimal("66300"),
        stop_price=Decimal("64350"),
        take_price=Decimal("66300"),
        exit_reason="take_profit",
        gross_return=Decimal("0.02"),
        net_return=Decimal("0.019"),
        fee_bps=Decimal("5"),
        slippage_bps=Decimal("1"),
        r_multiple=Decimal("2"),
    )


def _external_trade(
    *,
    entry_time: datetime | None = None,
    stop_price: Decimal = Decimal("64350"),
) -> QuantDingerReplayTrade:
    local = _local_trade()
    return QuantDingerReplayTrade(
        symbol="BTC/USDT",
        side="LONG",
        signal_candle_close_time=local.opened_at - timedelta(minutes=15),
        entry_time=entry_time or local.opened_at,
        entry_price=Decimal("65000"),
        stop_price=stop_price,
        take_price=Decimal("66300"),
        exit_time=local.closed_at,
        exit_price=Decimal("66300"),
        exit_reason="take_profit",
        fee_bps=Decimal("5"),
    )


def _artifact_payload(*, manifest_code_hash: str = "hash-1", warmup_bars: int = 80) -> dict[str, object]:
    trade = _external_trade()
    return {
        "schema_version": "1",
        "source_name": "QuantDinger",
        "source_version": "5.0.1",
        "manifest_code_hash": manifest_code_hash,
        "timeframe": "15m",
        "warmup_bars": warmup_bars,
        "trades": [
            {
                "symbol": trade.symbol,
                "side": trade.side,
                "signal_candle_close_time": trade.signal_candle_close_time.isoformat(),
                "entry_time": trade.entry_time.isoformat(),
                "entry_price": str(trade.entry_price),
                "stop_price": str(trade.stop_price),
                "take_price": str(trade.take_price),
                "exit_time": trade.exit_time.isoformat(),
                "exit_price": str(trade.exit_price),
                "exit_reason": trade.exit_reason,
                "fee_bps": str(trade.fee_bps),
            }
        ],
    }


def test_matching_external_shadow_replay_is_consistent() -> None:
    report = compare_quantdinger_replay(
        manifest_code_hash="hash-1",
        timeframe="15m",
        local_trades=(_local_trade(),),
        external_trades=(_external_trade(),),
    )

    assert report.is_consistent is True
    assert report.matched_trade_count == 1
    assert report.as_dict()["mismatches"] == []


def test_next_bar_violation_is_rejected_before_comparison() -> None:
    local = _local_trade()
    with pytest.raises(ValueError, match="after the closed signal bar"):
        _external_trade(entry_time=local.opened_at - timedelta(minutes=15))


def test_price_geometry_and_cost_mismatch_are_reported() -> None:
    external = _external_trade(stop_price=Decimal("64000"))
    report = compare_quantdinger_replay(
        manifest_code_hash="hash-1",
        timeframe="15m",
        local_trades=(_local_trade(),),
        external_trades=(external,),
    )

    assert report.is_consistent is False
    assert [item.code for item in report.mismatches] == [DifferentialMismatchCode.STOP_PRICE_MISMATCH]


def test_missing_external_next_bar_trade_is_a_material_mismatch() -> None:
    report = compare_quantdinger_replay(
        manifest_code_hash="hash-1",
        timeframe="15m",
        local_trades=(_local_trade(),),
        external_trades=(),
    )

    assert report.mismatches[0].code is DifferentialMismatchCode.LOCAL_TRADE_UNMATCHED


def test_isolated_replay_artifact_binds_to_manifest_then_feeds_comparator() -> None:
    artifact = parse_quantdinger_replay_artifact(
        _artifact_payload(),
        expected_manifest_code_hash="hash-1",
        expected_timeframe="15m",
        minimum_warmup_bars=80,
    )

    report = compare_quantdinger_replay(
        manifest_code_hash=artifact.manifest_code_hash,
        timeframe=artifact.timeframe,
        local_trades=(_local_trade(),),
        external_trades=artifact.trades,
    )

    assert artifact.warmup_bars == 80
    assert report.is_consistent is True


def test_replay_artifact_canonicalizes_external_symbol() -> None:
    payload = _artifact_payload()
    trade = payload["trades"][0]
    assert isinstance(trade, dict)
    trade["symbol"] = "btcusdt"

    artifact = parse_quantdinger_replay_artifact(
        payload,
        expected_manifest_code_hash="hash-1",
        expected_timeframe="15m",
        minimum_warmup_bars=80,
    )

    assert artifact.trades[0].symbol == "BTC/USDT"


@pytest.mark.parametrize(
    "payload_update, message",
    [
        ({"manifest_code_hash": "unrelated"}, "manifest_code_hash"),
        ({"warmup_bars": 79}, "warmup_bars"),
        ({"source_version": "mainnet"}, "source_version"),
    ],
)
def test_isolated_replay_artifact_fails_closed_when_contract_drifts(
    payload_update: dict[str, object],
    message: str,
) -> None:
    payload = _artifact_payload()
    payload.update(payload_update)

    with pytest.raises(QuantDingerReplayArtifactError, match=message):
        parse_quantdinger_replay_artifact(
            payload,
            expected_manifest_code_hash="hash-1",
            expected_timeframe="15m",
            minimum_warmup_bars=80,
        )


def test_replay_artifact_requires_exactly_one_timeframe_after_signal_close() -> None:
    payload = _artifact_payload()
    trade = payload["trades"][0]
    assert isinstance(trade, dict)
    trade["signal_candle_close_time"] = (_local_trade().opened_at - timedelta(minutes=30)).isoformat()

    with pytest.raises(QuantDingerReplayArtifactError, match="next bar"):
        parse_quantdinger_replay_artifact(
            payload,
            expected_manifest_code_hash="hash-1",
            expected_timeframe="15m",
            minimum_warmup_bars=80,
        )


def test_replay_artifact_rejects_unknown_metadata_fields() -> None:
    payload = _artifact_payload()
    payload["unreviewed_field"] = "must fail closed"

    with pytest.raises(QuantDingerReplayArtifactError, match="unexpected"):
        parse_quantdinger_replay_artifact(
            payload,
            expected_manifest_code_hash="hash-1",
            expected_timeframe="15m",
            minimum_warmup_bars=80,
        )


@pytest.mark.parametrize("tolerance", [Decimal("NaN"), Decimal("Infinity")])
def test_differential_replay_rejects_nonfinite_tolerance(tolerance: Decimal) -> None:
    with pytest.raises(ValueError, match="finite"):
        compare_quantdinger_replay(
            manifest_code_hash="hash-1",
            timeframe="15m",
            local_trades=(_local_trade(),),
            external_trades=(_external_trade(),),
            price_tolerance_bps=tolerance,
        )
