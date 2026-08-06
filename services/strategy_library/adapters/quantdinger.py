"""Static, shadow-only bridge for QuantDinger Strategy API V2 source.

This module deliberately compiles no third-party strategy code.  It parses a
small declarative subset with :mod:`ast`, records provenance, and translates a
validated external signal into a ``RESEARCH`` ``TradeCandidate``.  The V2 entry
service rejects those candidates before an exchange adapter can be called.
"""

from __future__ import annotations

import ast
import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from services.automated_trading.domain.candidates import EXECUTION_UNIVERSE, CandidateLane, TradeCandidate
from services.automated_trading.domain.enums import V2CandidateType
from services.automated_trading.observability.decision_funnel import (
    DecisionFunnelRecord,
    DecisionReasonCode,
    FunnelStage,
    StageOutcome,
    build_funnel_record,
)

SOURCE_NAME = "QuantDinger"
SOURCE_VERSION = "5.0.1"
_HANDLERS = frozenset({"handle_data", "on_rebalance"})
_FORBIDDEN_CALLS = frozenset(
    {
        "order",
        "order_value",
        "order_target",
        "order_target_percent",
        "order_target_value",
        "cancel_order",
    }
)
_VALID_TIMEFRAMES = frozenset({"1m", "5m", "15m", "1h", "4h", "1d"})


class QuantDingerContractError(ValueError):
    """Raised when source cannot be represented safely by the bridge."""


@dataclass(frozen=True)
class QuantDingerProtectionDeclaration:
    """Relative protection declarations discovered from static source."""

    stop_loss_pct: Decimal | None = None
    take_profit_pct: Decimal | None = None

    def __post_init__(self) -> None:
        if self.stop_loss_pct is not None and (
            not self.stop_loss_pct.is_finite() or not Decimal("0") < self.stop_loss_pct < Decimal("1")
        ):
            raise QuantDingerContractError("stop_loss_pct must be finite and between 0 and 1")
        if self.take_profit_pct is not None and (not self.take_profit_pct.is_finite() or self.take_profit_pct <= 0):
            raise QuantDingerContractError("take_profit_pct must be finite and > 0")


@dataclass(frozen=True)
class QuantDingerStrategyManifest:
    """Immutable provenance and static contract for one external strategy."""

    code_hash: str
    instruments: tuple[str, ...]
    timeframe: str
    warmup_bars: int
    factor_dependencies: tuple[str, ...]
    handlers: tuple[str, ...]
    protection: QuantDingerProtectionDeclaration
    source_name: str = SOURCE_NAME
    source_version: str = SOURCE_VERSION


@dataclass(frozen=True)
class QuantDingerSignal:
    """Validated runtime signal supplied by an offline/shadow evaluator only."""

    strategy_id: str
    strategy_version: str
    symbol: str
    timeframe: str
    side: str
    signal_candle_close_time: datetime
    signal_reference_price: Decimal
    confidence: Decimal
    stop_distance: Decimal
    take_profit_distance: Decimal | None
    max_entry_drift_bps: Decimal
    expires_at: datetime

    def __post_init__(self) -> None:
        canonical_symbol = _canonical_symbol(self.symbol)
        if canonical_symbol != self.symbol:
            object.__setattr__(self, "symbol", canonical_symbol)
        if not self.strategy_id.strip() or not self.strategy_version.strip():
            raise QuantDingerContractError("strategy_id and strategy_version are required")
        if self.timeframe.strip().lower() not in _VALID_TIMEFRAMES:
            raise QuantDingerContractError("unsupported signal timeframe")
        if self.side not in {"LONG", "SHORT"}:
            raise QuantDingerContractError("signal side must be LONG or SHORT")
        if not _is_timezone_aware(self.signal_candle_close_time) or not _is_timezone_aware(self.expires_at):
            raise QuantDingerContractError("signal timestamps must be timezone-aware")
        if self.expires_at <= self.signal_candle_close_time:
            raise QuantDingerContractError("signal expires_at must be after signal_candle_close_time")
        for name, value in (
            ("signal_reference_price", self.signal_reference_price),
            ("stop_distance", self.stop_distance),
            ("max_entry_drift_bps", self.max_entry_drift_bps),
        ):
            if not value.is_finite() or value <= 0:
                raise QuantDingerContractError(f"{name} must be finite and positive")
        if not self.confidence.is_finite() or not Decimal("0") <= self.confidence <= Decimal("1"):
            raise QuantDingerContractError("confidence must be finite and within [0, 1]")
        if self.take_profit_distance is not None and (
            not self.take_profit_distance.is_finite() or self.take_profit_distance <= 0
        ):
            raise QuantDingerContractError("take_profit_distance must be finite and positive when set")


def compile_strategy_manifest(
    source: str,
    *,
    allow_shadow_order_apis: bool = False,
) -> QuantDingerStrategyManifest:
    """Parse the safe declarative subset of Strategy API V2 source.

    The accepted subset is intentionally small: literal instruments, a literal
    subscription frequency, literal warmup/protection values and literal factor
    names. Imports and order APIs are rejected by default. The isolated Shadow
    worker may opt into order names only so it can capture intent as data; it
    never receives an exchange adapter.
    """
    if not source or not source.strip():
        raise QuantDingerContractError("strategy source is required")
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise QuantDingerContractError(f"invalid strategy syntax: {exc.msg}") from exc

    functions = {node.name: node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    if "initialize" not in functions:
        raise QuantDingerContractError("initialize handler is required")
    handlers = tuple(sorted(name for name in functions if name in _HANDLERS))
    if not handlers:
        raise QuantDingerContractError("handle_data or on_rebalance handler is required")

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            raise QuantDingerContractError("imports are not permitted in the static bridge")
        if isinstance(node, ast.Call) and _call_name(node) in _FORBIDDEN_CALLS and not allow_shadow_order_apis:
            raise QuantDingerContractError(f"execution API {_call_name(node)!r} is not permitted")

    instruments: tuple[str, ...] = ()
    timeframe: str | None = None
    warmup_bars = 0
    protection = QuantDingerProtectionDeclaration()
    for call in _calls(functions["initialize"]):
        name = _call_name(call)
        if name == "set_universe":
            instruments = _literal_instruments(call)
        elif name == "subscribe":
            timeframe = _literal_timeframe(call)
        elif name == "set_warmup":
            warmup_bars = _literal_non_negative_int(call, "set_warmup")
        elif name == "set_default_protection":
            protection = _literal_protection(call)

    for call in _calls(tree):
        if _call_name(call) not in _FORBIDDEN_CALLS:
            continue
        inline_protection = _literal_order_protection(call)
        if inline_protection is None:
            continue
        if protection != QuantDingerProtectionDeclaration() and protection != inline_protection:
            raise QuantDingerContractError("order protection differs from the declared manifest protection")
        protection = inline_protection

    if not instruments:
        raise QuantDingerContractError("a literal static universe is required")
    if timeframe is None:
        raise QuantDingerContractError("a literal subscription timeframe is required")

    factors = sorted(
        {
            _literal_first_string(call, _call_name(call))
            for call in _calls(tree)
            if _call_name(call) in {"factor", "indicator"}
        }
    )
    return QuantDingerStrategyManifest(
        code_hash=hashlib.sha256(source.encode("utf-8")).hexdigest(),
        instruments=instruments,
        timeframe=timeframe,
        warmup_bars=warmup_bars,
        factor_dependencies=tuple(factors),
        handlers=handlers,
        protection=protection,
    )


def build_shadow_candidate(
    manifest: QuantDingerStrategyManifest,
    signal: QuantDingerSignal,
    *,
    cycle_id: str,
) -> TradeCandidate:
    """Translate a signal into a non-executable V2 research candidate.

    This validates source identity, timeframe and fixed execution universe, but
    it does not promote a research strategy or calculate absolute protection
    prices.  The resulting candidate is always ``RESEARCH`` + ``SHADOW``.
    """
    symbol = _canonical_symbol(signal.symbol)
    if symbol not in manifest.instruments:
        raise QuantDingerContractError(f"signal symbol {symbol!r} is absent from manifest")
    if symbol not in EXECUTION_UNIVERSE:
        raise QuantDingerContractError(f"symbol {symbol!r} is outside the BTC/ETH execution universe")
    if signal.timeframe.strip().lower() != manifest.timeframe:
        raise QuantDingerContractError("signal timeframe differs from manifest timeframe")
    if signal.side not in {"LONG", "SHORT"}:
        raise QuantDingerContractError("signal side must be LONG or SHORT")
    if manifest.protection.stop_loss_pct is None:
        raise QuantDingerContractError("external strategy must declare a stop-loss before shadow conversion")
    expected_stop_distance = signal.signal_reference_price * manifest.protection.stop_loss_pct
    if signal.stop_distance != expected_stop_distance:
        raise QuantDingerContractError("signal stop distance differs from the declared protection rule")
    expected_take_distance = (
        signal.signal_reference_price * manifest.protection.take_profit_pct
        if manifest.protection.take_profit_pct is not None
        else None
    )
    if signal.take_profit_distance != expected_take_distance:
        raise QuantDingerContractError("signal take-profit distance differs from the declared protection rule")

    candidate_id = hashlib.sha256(
        f"{cycle_id}:{manifest.code_hash}:{signal.strategy_id}:{signal.symbol}:{signal.signal_candle_close_time.isoformat()}".encode()
    ).hexdigest()[:32]
    return TradeCandidate(
        candidate_id=candidate_id,
        cycle_id=cycle_id,
        strategy_id=signal.strategy_id,
        strategy_version=signal.strategy_version,
        lane=CandidateLane.SHADOW,
        candidate_type=V2CandidateType.RESEARCH,
        symbol=symbol,
        side=signal.side,
        signal_candle_close_time=signal.signal_candle_close_time,
        signal_reference_price=signal.signal_reference_price,
        confidence=signal.confidence,
        stop_distance=signal.stop_distance,
        take_profit_distance=signal.take_profit_distance,
        max_entry_drift_bps=signal.max_entry_drift_bps,
        expires_at=signal.expires_at,
        non_promotable=True,
        signal_context=(
            ("source", SOURCE_NAME),
            ("source_version", manifest.source_version),
            ("code_hash", manifest.code_hash),
            ("timeframe", manifest.timeframe),
            ("lane", CandidateLane.SHADOW),
        ),
    )


def parse_shadow_signal(
    payload: Mapping[str, object],
    *,
    manifest: QuantDingerStrategyManifest,
) -> QuantDingerSignal:
    """Decode one offline Shadow signal without running external source code.

    The event carries the compiled source hash so an output generated for one
    strategy cannot be attributed to another. This is an ingestion boundary,
    not a strategy runtime: callers may pass artifacts from an isolated worker,
    but this process never evaluates arbitrary QuantDinger source.
    """
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
    _require_exact_keys(payload, required, "shadow signal")
    if _require_string(payload, "manifest_code_hash") != manifest.code_hash:
        raise QuantDingerContractError("shadow signal manifest_code_hash does not match the compiled source")

    signal = QuantDingerSignal(
        strategy_id=_require_string(payload, "strategy_id"),
        strategy_version=_require_string(payload, "strategy_version"),
        symbol=_canonical_symbol(_require_string(payload, "symbol")),
        timeframe=_require_string(payload, "timeframe"),
        side=_require_string(payload, "side"),
        signal_candle_close_time=_require_timestamp(payload, "signal_candle_close_time"),
        signal_reference_price=_require_decimal(payload, "signal_reference_price"),
        confidence=_require_decimal(payload, "confidence"),
        stop_distance=_require_decimal(payload, "stop_distance"),
        take_profit_distance=_optional_decimal(payload, "take_profit_distance"),
        max_entry_drift_bps=_require_decimal(payload, "max_entry_drift_bps"),
        expires_at=_require_timestamp(payload, "expires_at"),
    )
    if signal.symbol not in manifest.instruments:
        raise QuantDingerContractError("shadow signal symbol is absent from manifest")
    if signal.timeframe.strip().lower() != manifest.timeframe:
        raise QuantDingerContractError("shadow signal timeframe differs from manifest")
    return signal


def build_shadow_funnel_record(candidate: TradeCandidate) -> DecisionFunnelRecord:
    """Record an external candidate in the V2 funnel without creating intent.

    The Shadow lane ends at manifest observation. It intentionally never
    reaches risk, intent, exchange, position, or protection stages, so an
    external signal is visible to operators without becoming an order path.
    """
    if candidate.lane is not CandidateLane.SHADOW or candidate.candidate_type is not V2CandidateType.RESEARCH:
        raise QuantDingerContractError("only RESEARCH SHADOW candidates may enter the Shadow funnel")
    context = dict(candidate.signal_context)
    if context.get("source") != SOURCE_NAME or context.get("code_hash") is None:
        raise QuantDingerContractError("Shadow funnel requires QuantDinger source and code hash provenance")

    builder = build_funnel_record(
        cycle_id=candidate.cycle_id,
        symbol=candidate.symbol,
        lane=candidate.lane.value,
        strategy_id=candidate.strategy_id,
        strategy_version=candidate.strategy_version,
    )
    builder.record(FunnelStage.CYCLE_STARTED, StageOutcome.PASSED, metrics={"source": SOURCE_NAME})
    builder.set_decision_bar(candidate.signal_candle_close_time)
    builder.record(
        FunnelStage.CANDLE_CLOSED,
        StageOutcome.PASSED,
        metrics={"bar_timestamp": candidate.signal_candle_close_time.isoformat()},
    )
    builder.set_candidate(candidate.candidate_id)
    builder.record(
        FunnelStage.CANDIDATE_CREATED,
        StageOutcome.PASSED,
        metrics={
            "candidate_id": candidate.candidate_id,
            "code_hash": context["code_hash"],
            "non_promotable": candidate.non_promotable,
        },
    )
    builder.record(
        FunnelStage.MANIFEST_EVALUATED,
        StageOutcome.SKIPPED,
        DecisionReasonCode.SHADOW_MODE_NO_SUBMIT,
        detail="external QuantDinger candidate is retained for Shadow observation only",
    )
    return builder.build(
        terminal_stage=FunnelStage.MANIFEST_EVALUATED,
        reason_code=DecisionReasonCode.SHADOW_MODE_NO_SUBMIT,
    )


def _calls(node: ast.AST) -> tuple[ast.Call, ...]:
    return tuple(item for item in ast.walk(node) if isinstance(item, ast.Call))


def _call_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return ""


def _literal_instruments(call: ast.Call) -> tuple[str, ...]:
    if not call.args:
        raise QuantDingerContractError("set_universe requires literal instruments")
    raw = _literal_eval(call.args[0], "set_universe")
    values = (raw,) if isinstance(raw, str) else raw
    if not isinstance(values, (list, tuple)) or not values or not all(isinstance(item, str) for item in values):
        raise QuantDingerContractError("set_universe requires a non-empty literal string list")
    return tuple(dict.fromkeys(_canonical_symbol(item) for item in values))


def _literal_timeframe(call: ast.Call) -> str:
    frequency = next((keyword.value for keyword in call.keywords if keyword.arg == "frequency"), None)
    if frequency is None:
        raise QuantDingerContractError("subscribe requires a literal frequency")
    value = _literal_eval(frequency, "subscribe frequency")
    if not isinstance(value, str) or value.strip().lower() not in _VALID_TIMEFRAMES:
        raise QuantDingerContractError("unsupported subscription timeframe")
    return value.strip().lower()


def _literal_non_negative_int(call: ast.Call, name: str) -> int:
    if not call.args:
        raise QuantDingerContractError(f"{name} requires a literal bar count")
    value = _literal_eval(call.args[0], name)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise QuantDingerContractError(f"{name} requires a non-negative integer")
    return value


def _literal_protection(call: ast.Call) -> QuantDingerProtectionDeclaration:
    values = {
        keyword.arg: _literal_eval(keyword.value, "set_default_protection")
        for keyword in call.keywords
        if keyword.arg is not None
    }
    return QuantDingerProtectionDeclaration(
        stop_loss_pct=_decimal_or_none(values.get("stop_loss_pct")),
        take_profit_pct=_decimal_or_none(values.get("take_profit_pct")),
    )


def _literal_order_protection(call: ast.Call) -> QuantDingerProtectionDeclaration | None:
    values = {
        keyword.arg: _literal_eval(keyword.value, "order protection")
        for keyword in call.keywords
        if keyword.arg in {"stop_loss_pct", "take_profit_pct"}
    }
    if not values:
        return None
    return QuantDingerProtectionDeclaration(
        stop_loss_pct=_decimal_or_none(values.get("stop_loss_pct")),
        take_profit_pct=_decimal_or_none(values.get("take_profit_pct")),
    )


def _literal_first_string(call: ast.Call, name: str) -> str:
    if not call.args:
        raise QuantDingerContractError(f"{name} requires a literal factor name")
    value = _literal_eval(call.args[0], name)
    if not isinstance(value, str) or not value.strip():
        raise QuantDingerContractError(f"{name} requires a non-empty literal factor name")
    return value.strip()


def _decimal_or_none(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception as exc:  # noqa: BLE001
        raise QuantDingerContractError("protection value must be numeric") from exc


def _literal_eval(node: ast.AST, context: str) -> object:
    try:
        return ast.literal_eval(node)
    except (TypeError, ValueError) as exc:
        raise QuantDingerContractError(f"{context} must use literal values") from exc


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


def _require_exact_keys(payload: Mapping[str, object], required: set[str], context: str) -> None:
    missing = sorted(required - payload.keys())
    unexpected = sorted(payload.keys() - required)
    if missing or unexpected:
        raise QuantDingerContractError(f"{context} schema mismatch: missing={missing}, unexpected={unexpected}")


def _require_string(payload: Mapping[str, object], field: str) -> str:
    value = payload[field]
    if not isinstance(value, str) or not value.strip():
        raise QuantDingerContractError(f"{field} must be a non-empty string")
    return value


def _require_timestamp(payload: Mapping[str, object], field: str) -> datetime:
    value = payload[field]
    if not isinstance(value, str):
        raise QuantDingerContractError(f"{field} must be an ISO-8601 timestamp string")
    try:
        timestamp = datetime.fromisoformat(value)
    except ValueError as exc:
        raise QuantDingerContractError(f"{field} must be an ISO-8601 timestamp string") from exc
    if not _is_timezone_aware(timestamp):
        raise QuantDingerContractError(f"{field} must be timezone-aware")
    return timestamp


def _require_decimal(payload: Mapping[str, object], field: str) -> Decimal:
    value = payload[field]
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise QuantDingerContractError(f"{field} must be numeric")
    try:
        decimal_value = Decimal(str(value))
    except Exception as exc:  # noqa: BLE001
        raise QuantDingerContractError(f"{field} must be numeric") from exc
    if not decimal_value.is_finite():
        raise QuantDingerContractError(f"{field} must be finite")
    return decimal_value


def _optional_decimal(payload: Mapping[str, object], field: str) -> Decimal | None:
    return None if payload[field] is None else _require_decimal(payload, field)


def _is_timezone_aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None
