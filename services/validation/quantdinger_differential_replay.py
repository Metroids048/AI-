"""Compare external Shadow replay output with authoritative local replay trades.

The external runtime is deliberately not executed here.  A caller supplies its
already-produced, structured observations and this module compares them with
``TechnicalStrategyValidationService`` output.  A mismatch is evidence to fix
the adapter or external runtime, never an input to strategy promotion.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from services.validation.technical_replay import ReplayTrade


class DifferentialMismatchCode(StrEnum):
    """Stable categories for semantic disagreement between replay engines."""

    LOCAL_TRADE_UNMATCHED = "LOCAL_TRADE_UNMATCHED"
    EXTERNAL_TRADE_UNMATCHED = "EXTERNAL_TRADE_UNMATCHED"
    ENTRY_PRICE_MISMATCH = "ENTRY_PRICE_MISMATCH"
    STOP_PRICE_MISMATCH = "STOP_PRICE_MISMATCH"
    TAKE_PRICE_MISMATCH = "TAKE_PRICE_MISMATCH"
    EXIT_TIME_MISMATCH = "EXIT_TIME_MISMATCH"
    EXIT_PRICE_MISMATCH = "EXIT_PRICE_MISMATCH"
    EXIT_REASON_MISMATCH = "EXIT_REASON_MISMATCH"
    FEE_MISMATCH = "FEE_MISMATCH"


class QuantDingerReplayArtifactError(ValueError):
    """Raised when an offline QuantDinger replay artifact is not trustworthy."""


@dataclass(frozen=True)
class QuantDingerReplayTrade:
    """One external replay result expressed in the local validation vocabulary.

    ``entry_time`` is the first executable next-bar instant, not the signal
    close.  Requiring it to follow ``signal_candle_close_time`` makes a
    look-ahead replay invalid before it can be compared.
    """

    symbol: str
    side: str
    signal_candle_close_time: datetime
    entry_time: datetime
    entry_price: Decimal
    stop_price: Decimal
    take_price: Decimal
    exit_time: datetime
    exit_price: Decimal
    exit_reason: str
    fee_bps: Decimal

    def __post_init__(self) -> None:
        canonical_symbol = _canonical_symbol(self.symbol)
        if canonical_symbol != self.symbol:
            object.__setattr__(self, "symbol", canonical_symbol)
        if not self.symbol.strip():
            raise ValueError("external replay symbol is required")
        if self.side not in {"LONG", "SHORT"}:
            raise ValueError("external replay side must be LONG or SHORT")
        if not _is_timezone_aware(self.signal_candle_close_time) or not _is_timezone_aware(self.entry_time):
            raise ValueError("external replay signal and entry times must be timezone-aware")
        if self.entry_time <= self.signal_candle_close_time:
            raise ValueError("external replay entry_time must be after the closed signal bar")
        if not _is_timezone_aware(self.exit_time) or self.exit_time < self.entry_time:
            raise ValueError("external replay exit_time must be timezone-aware and not precede entry_time")
        for name, value in (
            ("entry_price", self.entry_price),
            ("stop_price", self.stop_price),
            ("take_price", self.take_price),
        ):
            if not value.is_finite() or value <= 0:
                raise ValueError(f"external replay {name} must be finite and positive")
        if not self.exit_price.is_finite() or self.exit_price <= 0:
            raise ValueError("external replay exit_price must be finite and positive")
        if not self.fee_bps.is_finite() or self.fee_bps < 0:
            raise ValueError("external replay fee_bps must be finite and non-negative")

    @property
    def key(self) -> tuple[str, str, datetime]:
        return self.symbol, self.side.lower(), self.entry_time


@dataclass(frozen=True)
class QuantDingerReplayArtifact:
    """Validated output from an isolated external replay producer.

    This object only carries data. Parsing it never imports, compiles, or runs
    a third-party strategy, and a consistent comparison remains validation
    evidence rather than a strategy-promotion decision.
    """

    manifest_code_hash: str
    timeframe: str
    warmup_bars: int
    trades: tuple[QuantDingerReplayTrade, ...]


@dataclass(frozen=True)
class DifferentialMismatch:
    """One material deviation from the existing authoritative replay."""

    code: DifferentialMismatchCode
    symbol: str
    side: str
    entry_time: datetime
    detail: str


@dataclass(frozen=True)
class QuantDingerDifferentialReplayReport:
    """Immutable comparison report.  ``is_consistent`` is never a promotion."""

    manifest_code_hash: str
    local_trade_count: int
    external_trade_count: int
    matched_trade_count: int
    mismatches: tuple[DifferentialMismatch, ...]

    @property
    def is_consistent(self) -> bool:
        return not self.mismatches

    def as_dict(self) -> dict[str, object]:
        return {
            "manifest_code_hash": self.manifest_code_hash,
            "local_trade_count": self.local_trade_count,
            "external_trade_count": self.external_trade_count,
            "matched_trade_count": self.matched_trade_count,
            "is_consistent": self.is_consistent,
            "mismatches": [
                {
                    "code": mismatch.code.value,
                    "symbol": mismatch.symbol,
                    "side": mismatch.side,
                    "entry_time": mismatch.entry_time.isoformat(),
                    "detail": mismatch.detail,
                }
                for mismatch in self.mismatches
            ],
        }


def compare_quantdinger_replay(
    *,
    manifest_code_hash: str,
    timeframe: str,
    local_trades: Iterable[ReplayTrade],
    external_trades: Iterable[QuantDingerReplayTrade],
    price_tolerance_bps: Decimal = Decimal("0"),
    fee_tolerance_bps: Decimal = Decimal("0"),
) -> QuantDingerDifferentialReplayReport:
    """Compare exact next-bar replay semantics without executing either strategy.

    Local ``ReplayTrade`` records are authoritative.  Equal entry keys are
    necessary but insufficient: price geometry, exit timing, reason and costs
    must also match within the explicitly passed tolerances.
    """
    _validate_tolerance(price_tolerance_bps, "price_tolerance_bps")
    _validate_tolerance(fee_tolerance_bps, "fee_tolerance_bps")
    interval = _timeframe_interval(timeframe)
    local = tuple(local_trades)
    external = tuple(external_trades)
    for trade in external:
        _validate_next_bar_semantics(trade, interval)
    local_by_key = _unique_local_index(local)
    external_by_key = _unique_external_index(external)
    mismatches: list[DifferentialMismatch] = []
    matched = 0

    for key, external_trade in external_by_key.items():
        local_trade = local_by_key.get(key)
        if local_trade is None:
            mismatches.append(
                DifferentialMismatch(
                    code=DifferentialMismatchCode.EXTERNAL_TRADE_UNMATCHED,
                    symbol=external_trade.symbol,
                    side=external_trade.side,
                    entry_time=external_trade.entry_time,
                    detail="no authoritative local trade opened on the external next-bar entry time",
                )
            )
            continue
        matched += 1
        mismatches.extend(
            _compare_trade(
                local_trade=local_trade,
                external_trade=external_trade,
                price_tolerance_bps=price_tolerance_bps,
                fee_tolerance_bps=fee_tolerance_bps,
            )
        )

    for key, local_trade in local_by_key.items():
        if key not in external_by_key:
            mismatches.append(
                DifferentialMismatch(
                    code=DifferentialMismatchCode.LOCAL_TRADE_UNMATCHED,
                    symbol=local_trade.symbol,
                    side=local_trade.side.value,
                    entry_time=local_trade.opened_at,
                    detail="no external replay trade opened on the authoritative next-bar entry time",
                )
            )

    return QuantDingerDifferentialReplayReport(
        manifest_code_hash=manifest_code_hash,
        local_trade_count=len(local),
        external_trade_count=len(external),
        matched_trade_count=matched,
        mismatches=tuple(mismatches),
    )


def parse_quantdinger_replay_artifact(
    payload: Mapping[str, object],
    *,
    expected_manifest_code_hash: str,
    expected_timeframe: str,
    minimum_warmup_bars: int,
    expected_source_version: str = "5.0.1",
) -> QuantDingerReplayArtifact:
    """Validate a structured artifact emitted by an isolated Shadow worker.

    The caller supplies the manifest contract obtained from the static bridge.
    This keeps the validation layer independent of strategy-source parsing and
    prevents output for another code hash, timeframe, or inadequate warmup
    from being treated as parity evidence.
    """
    required = {
        "schema_version",
        "source_name",
        "source_version",
        "manifest_code_hash",
        "timeframe",
        "warmup_bars",
        "trades",
    }
    optional = {
        "strategy_id",
        "strategy_version",
        "signals",
        "rejected_events",
        "stop_loss_pct",
        "take_profit_pct",
    }
    _require_exact_keys(payload, required, "replay artifact", optional=optional)
    if _require_string(payload, "schema_version") != "1":
        raise QuantDingerReplayArtifactError("unsupported replay artifact schema_version")
    if _require_string(payload, "source_name") != "QuantDinger":
        raise QuantDingerReplayArtifactError("replay artifact source_name must be QuantDinger")
    if _require_string(payload, "source_version") != expected_source_version:
        raise QuantDingerReplayArtifactError("replay artifact source_version differs from the locked bridge")
    manifest_code_hash = _require_string(payload, "manifest_code_hash")
    if manifest_code_hash != expected_manifest_code_hash:
        raise QuantDingerReplayArtifactError("replay artifact manifest_code_hash does not match the compiled source")
    timeframe = _require_string(payload, "timeframe").lower()
    if timeframe != expected_timeframe.lower():
        raise QuantDingerReplayArtifactError("replay artifact timeframe differs from the compiled manifest")
    warmup_bars = _require_non_negative_int(payload, "warmup_bars")
    if warmup_bars < minimum_warmup_bars:
        raise QuantDingerReplayArtifactError("replay artifact warmup_bars is below the compiled manifest minimum")
    for field in ("strategy_id", "strategy_version"):
        if field in payload:
            _require_string(payload, field)
    stop_loss_pct = _optional_protection_decimal(payload, "stop_loss_pct")
    take_profit_pct = _optional_protection_decimal(payload, "take_profit_pct")
    for field in ("signals", "rejected_events"):
        if field in payload:
            value = payload[field]
            if not isinstance(value, list) or not all(isinstance(item, Mapping) for item in value):
                raise QuantDingerReplayArtifactError(f"{field} must be a list of objects")
    if "signals" in payload:
        _validate_signal_metadata(
            payload["signals"],
            manifest_code_hash=manifest_code_hash,
            stop_loss_pct=stop_loss_pct,
            take_profit_pct=take_profit_pct,
        )
    raw_trades = payload["trades"]
    if not isinstance(raw_trades, list):
        raise QuantDingerReplayArtifactError("replay artifact trades must be a list")
    trades = tuple(_parse_trade(item) for item in raw_trades)
    interval = _timeframe_interval(timeframe)
    for trade in trades:
        _validate_next_bar_semantics(trade, interval)
    _unique_external_index(trades)
    return QuantDingerReplayArtifact(
        manifest_code_hash=manifest_code_hash,
        timeframe=timeframe,
        warmup_bars=warmup_bars,
        trades=trades,
    )


def _unique_local_index(trades: tuple[ReplayTrade, ...]) -> dict[tuple[str, str, datetime], ReplayTrade]:
    index: dict[tuple[str, str, datetime], ReplayTrade] = {}
    for trade in trades:
        key = trade.symbol, trade.side.value, trade.opened_at
        if key in index:
            raise ValueError(f"duplicate local replay trade key: {key}")
        index[key] = trade
    return index


def _unique_external_index(
    trades: tuple[QuantDingerReplayTrade, ...],
) -> dict[tuple[str, str, datetime], QuantDingerReplayTrade]:
    index: dict[tuple[str, str, datetime], QuantDingerReplayTrade] = {}
    for trade in trades:
        if trade.key in index:
            raise ValueError(f"duplicate external replay trade key: {trade.key}")
        index[trade.key] = trade
    return index


def _compare_trade(
    *,
    local_trade: ReplayTrade,
    external_trade: QuantDingerReplayTrade,
    price_tolerance_bps: Decimal,
    fee_tolerance_bps: Decimal,
) -> list[DifferentialMismatch]:
    mismatches: list[DifferentialMismatch] = []
    values = (
        (
            DifferentialMismatchCode.ENTRY_PRICE_MISMATCH,
            "entry_price",
            local_trade.entry_price,
            external_trade.entry_price,
        ),
        (
            DifferentialMismatchCode.STOP_PRICE_MISMATCH,
            "stop_price",
            local_trade.stop_price,
            external_trade.stop_price,
        ),
        (
            DifferentialMismatchCode.TAKE_PRICE_MISMATCH,
            "take_price",
            local_trade.take_price,
            external_trade.take_price,
        ),
        (
            DifferentialMismatchCode.EXIT_PRICE_MISMATCH,
            "exit_price",
            local_trade.exit_price,
            external_trade.exit_price,
        ),
    )
    for code, label, local_value, external_value in values:
        delta_bps = _difference_bps(Decimal(str(local_value)), external_value)
        if delta_bps > price_tolerance_bps:
            mismatches.append(
                _mismatch(
                    code,
                    external_trade,
                    f"{label} differs by {delta_bps} bps (local={local_value}, external={external_value})",
                )
            )
    if local_trade.closed_at != external_trade.exit_time:
        mismatches.append(
            _mismatch(DifferentialMismatchCode.EXIT_TIME_MISMATCH, external_trade, "exit timestamps differ")
        )
    if local_trade.exit_reason != external_trade.exit_reason:
        mismatches.append(
            _mismatch(
                DifferentialMismatchCode.EXIT_REASON_MISMATCH,
                external_trade,
                f"exit reason differs (local={local_trade.exit_reason}, external={external_trade.exit_reason})",
            )
        )
    fee_delta = abs(Decimal(str(local_trade.fee_bps)) - external_trade.fee_bps)
    if fee_delta > fee_tolerance_bps:
        mismatches.append(
            _mismatch(
                DifferentialMismatchCode.FEE_MISMATCH,
                external_trade,
                f"fee differs by {fee_delta} bps (local={local_trade.fee_bps}, external={external_trade.fee_bps})",
            )
        )
    return mismatches


def _difference_bps(local_value: Decimal, external_value: Decimal) -> Decimal:
    if local_value <= 0:
        raise ValueError("authoritative local replay price must be positive")
    return abs(local_value - external_value) / local_value * Decimal("10000")


def _mismatch(
    code: DifferentialMismatchCode,
    trade: QuantDingerReplayTrade,
    detail: str,
) -> DifferentialMismatch:
    return DifferentialMismatch(code, trade.symbol, trade.side, trade.entry_time, detail)


def _parse_trade(payload: object) -> QuantDingerReplayTrade:
    if not isinstance(payload, Mapping):
        raise QuantDingerReplayArtifactError("replay artifact trade must be an object")
    required = {
        "symbol",
        "side",
        "signal_candle_close_time",
        "entry_time",
        "entry_price",
        "stop_price",
        "take_price",
        "exit_time",
        "exit_price",
        "exit_reason",
        "fee_bps",
    }
    _require_exact_keys(payload, required, "replay trade")
    try:
        return QuantDingerReplayTrade(
            symbol=_canonical_symbol(_require_string(payload, "symbol")),
            side=_require_string(payload, "side"),
            signal_candle_close_time=_require_timestamp(payload, "signal_candle_close_time"),
            entry_time=_require_timestamp(payload, "entry_time"),
            entry_price=_require_decimal(payload, "entry_price"),
            stop_price=_require_decimal(payload, "stop_price"),
            take_price=_require_decimal(payload, "take_price"),
            exit_time=_require_timestamp(payload, "exit_time"),
            exit_price=_require_decimal(payload, "exit_price"),
            exit_reason=_require_string(payload, "exit_reason"),
            fee_bps=_require_decimal(payload, "fee_bps"),
        )
    except ValueError as exc:
        raise QuantDingerReplayArtifactError(str(exc)) from exc


def _require_exact_keys(
    payload: Mapping[str, object],
    required: set[str],
    context: str,
    *,
    optional: set[str] | None = None,
) -> None:
    missing = sorted(required - payload.keys())
    unexpected = sorted(payload.keys() - required - (optional or set()))
    if missing or unexpected:
        raise QuantDingerReplayArtifactError(f"{context} schema mismatch: missing={missing}, unexpected={unexpected}")


def _require_string(payload: Mapping[str, object], field: str) -> str:
    value = payload[field]
    if not isinstance(value, str) or not value.strip():
        raise QuantDingerReplayArtifactError(f"{field} must be a non-empty string")
    return value


def _require_timestamp(payload: Mapping[str, object], field: str) -> datetime:
    value = payload[field]
    if not isinstance(value, str):
        raise QuantDingerReplayArtifactError(f"{field} must be an ISO-8601 timestamp string")
    try:
        timestamp = datetime.fromisoformat(value)
    except ValueError as exc:
        raise QuantDingerReplayArtifactError(f"{field} must be an ISO-8601 timestamp string") from exc
    if not _is_timezone_aware(timestamp):
        raise QuantDingerReplayArtifactError(f"{field} must be timezone-aware")
    return timestamp


def _require_decimal(payload: Mapping[str, object], field: str) -> Decimal:
    value = payload[field]
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise QuantDingerReplayArtifactError(f"{field} must be numeric")
    try:
        decimal_value = Decimal(str(value))
    except Exception as exc:  # noqa: BLE001
        raise QuantDingerReplayArtifactError(f"{field} must be numeric") from exc
    if not decimal_value.is_finite():
        raise QuantDingerReplayArtifactError(f"{field} must be finite")
    return decimal_value


def _require_non_negative_int(payload: Mapping[str, object], field: str) -> int:
    value = payload[field]
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise QuantDingerReplayArtifactError(f"{field} must be a non-negative integer")
    return value


def _optional_protection_decimal(payload: Mapping[str, object], field: str) -> Decimal | None:
    if field not in payload or payload[field] is None:
        return None
    value = _require_decimal(payload, field)
    if value <= 0 or (field == "stop_loss_pct" and value >= 1):
        raise QuantDingerReplayArtifactError(f"{field} must be positive and within protection bounds")
    return value


def _validate_signal_metadata(
    raw: object,
    *,
    manifest_code_hash: str,
    stop_loss_pct: Decimal | None,
    take_profit_pct: Decimal | None,
) -> None:
    if not isinstance(raw, list):
        raise QuantDingerReplayArtifactError("signals must be a list")
    required = {
        "manifest_code_hash",
        "strategy_id",
        "strategy_version",
        "symbol",
        "timeframe",
        "side",
        "signal_candle_close_time",
        "signal_reference_price",
        "confidence",
        "stop_distance",
        "take_profit_distance",
        "max_entry_drift_bps",
        "expires_at",
    }
    for item in raw:
        if not isinstance(item, Mapping):
            raise QuantDingerReplayArtifactError("signals must contain objects")
        _require_exact_keys(item, required, "replay artifact signal")
        if _require_string(item, "manifest_code_hash") != manifest_code_hash:
            raise QuantDingerReplayArtifactError("replay artifact signal manifest_code_hash does not match artifact")
        if stop_loss_pct is not None:
            expected_stop = _require_decimal(item, "signal_reference_price") * stop_loss_pct
            if _require_decimal(item, "stop_distance") != expected_stop:
                raise QuantDingerReplayArtifactError("replay artifact signal stop distance differs from protection")
        if take_profit_pct is not None:
            expected_take = _require_decimal(item, "signal_reference_price") * take_profit_pct
            if _require_decimal(item, "take_profit_distance") != expected_take:
                raise QuantDingerReplayArtifactError("replay artifact signal take distance differs from protection")


def _is_timezone_aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _canonical_symbol(value: str) -> str:
    raw = value.strip().upper()
    if ":" in raw:
        prefix, suffix = raw.split(":", 1)
        raw = suffix if prefix == "CRYPTO" else prefix
    raw = raw.split("@", 1)[0]
    if "/" in raw:
        return raw
    if raw.endswith("USDT"):
        return f"{raw[:-4]}/USDT"
    return raw


def _validate_tolerance(value: Decimal, field: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ValueError(f"{field} must be a finite, non-negative Decimal")


def _timeframe_interval(timeframe: str) -> timedelta:
    intervals = {
        "1m": timedelta(minutes=1),
        "5m": timedelta(minutes=5),
        "15m": timedelta(minutes=15),
        "1h": timedelta(hours=1),
        "4h": timedelta(hours=4),
        "1d": timedelta(days=1),
    }
    try:
        return intervals[timeframe.lower()]
    except (AttributeError, KeyError) as exc:
        raise ValueError(f"unsupported replay timeframe: {timeframe!r}") from exc


def _validate_next_bar_semantics(trade: QuantDingerReplayTrade, interval: timedelta) -> None:
    if trade.entry_time != trade.signal_candle_close_time + interval:
        raise QuantDingerReplayArtifactError("external replay entry_time must equal the next bar after signal close")
    interval_seconds = int(interval.total_seconds())
    signal_epoch_seconds = int(trade.signal_candle_close_time.timestamp())
    if signal_epoch_seconds % interval_seconds:
        raise QuantDingerReplayArtifactError("external replay signal_candle_close_time is not aligned to timeframe")
