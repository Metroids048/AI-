"""Market Intelligence factor generation.

This Data Layer service normalizes market/news/macro/provider evidence into a
bounded Strategy Layer vote. It intentionally does not create orders.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

from services.data.repository import DataRepository
from shared.config import settings
from shared.models import (
    MarketEvent,
    MarketExtras,
    MarketIntelligenceFeatureSnapshot,
    MarketIntelligenceProviderStatus,
    MarketIntelligenceSignal,
    RiskEvent,
    RiskLevel,
    RiskSeverity,
    TradeSide,
)


class CoinGlassProvider:
    name = "coinglass"

    def status(self) -> MarketIntelligenceProviderStatus:
        configured = bool(settings.coinglass_api_key)
        return MarketIntelligenceProviderStatus(
            provider=self.name,
            enabled=configured,
            configured=configured,
            status="ok" if configured else "missing_credentials",
            detail={"base_url": settings.coinglass_api_base_url},
        )

    @staticmethod
    def normalize_derivatives_payload(symbol: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "symbol": symbol,
            "open_interest": _float_or_none(payload.get("open_interest") or payload.get("openInterest")),
            "funding_rate": _float_or_none(payload.get("funding_rate") or payload.get("fundingRate")),
            "long_ratio": _float_or_none(payload.get("long_ratio") or payload.get("longRatio")),
            "short_ratio": _float_or_none(payload.get("short_ratio") or payload.get("shortRatio")),
            "liquidation_usd": _float_or_none(payload.get("liquidation_usd") or payload.get("liquidationUsd")),
        }


class CryptoQuantProvider:
    name = "cryptoquant"

    def status(self) -> MarketIntelligenceProviderStatus:
        configured = bool(settings.cryptoquant_api_key)
        return MarketIntelligenceProviderStatus(
            provider=self.name,
            enabled=configured,
            configured=configured,
            status="ok" if configured else "missing_credentials",
            detail={"base_url": settings.cryptoquant_api_base_url},
        )

    @staticmethod
    def normalize_exchange_flow_payload(payload: dict[str, Any]) -> dict[str, float | None]:
        inflow = _float_or_none(payload.get("exchange_inflow") or payload.get("inflow"))
        outflow = _float_or_none(payload.get("exchange_outflow") or payload.get("outflow"))
        reserve = _float_or_none(payload.get("stablecoin_reserve") or payload.get("stablecoinReserve"))
        return {
            "exchange_inflow_score": _bounded_score(-(inflow or 0.0), scale=10_000.0) if inflow is not None else None,
            "exchange_outflow_score": _bounded_score(outflow or 0.0, scale=10_000.0) if outflow is not None else None,
            "stablecoin_reserve_score": (
                _bounded_score(reserve or 0.0, scale=10_000_000.0) if reserve is not None else None
            ),
        }


class DeFiLlamaProvider:
    name = "defillama"

    def status(self) -> MarketIntelligenceProviderStatus:
        return MarketIntelligenceProviderStatus(
            provider=self.name,
            enabled=True,
            configured=True,
            status="ok",
            detail={"base_url": settings.defillama_api_base_url, "credential": "not_required"},
        )

    @staticmethod
    def normalize_protocol_growth_payload(payload: dict[str, Any]) -> dict[str, float | None]:
        growth = _float_or_none(payload.get("tvl_growth_7d") or payload.get("growth_7d"))
        stablecoins = _float_or_none(payload.get("stablecoin_growth_7d"))
        scores = [
            value
            for value in (
                _bounded_score(growth or 0.0, scale=25.0) if growth is not None else None,
                _bounded_score(stablecoins or 0.0, scale=25.0) if stablecoins is not None else None,
            )
            if value is not None
        ]
        return {"defi_growth_score": sum(scores) / len(scores) if scores else None}


class MarketIntelligenceService:
    """Build Market Intelligence events, feature snapshots, and capped votes."""

    def __init__(self, *, data_repo: DataRepository) -> None:
        self.data_repo = data_repo
        self.providers: list[CoinGlassProvider | CryptoQuantProvider | DeFiLlamaProvider] = [
            CoinGlassProvider(),
            CryptoQuantProvider(),
            DeFiLlamaProvider(),
        ]

    def provider_status(self) -> dict[str, dict[str, Any]]:
        rows = [
            MarketIntelligenceProviderStatus(
                provider="binance",
                enabled=True,
                configured=True,
                status="ok",
                detail={"source": "existing market_extras/ohlcv_bars"},
            ),
            *[provider.status() for provider in self.providers],
            MarketIntelligenceProviderStatus(
                provider="news_macro",
                enabled=True,
                configured=True,
                status="ok",
                detail={"source": "news_items/macro_events/risk_events"},
            ),
        ]
        return {row.provider: row.model_dump(mode="json") for row in rows}

    def list_events(self, *, symbol: str | None = None, limit: int = 50) -> list[MarketEvent]:
        events: list[MarketEvent] = []
        for event in self.data_repo.list_risk_events(active_only=False):
            if symbol and event.affected_scope and symbol not in event.affected_scope:
                continue
            events.append(_risk_event_to_market_event(event))
        for item in self.data_repo.list_news_items(limit=limit):
            if symbol and item.get("affected_symbols") and symbol not in item["affected_symbols"]:
                continue
            events.append(_news_item_to_market_event(item))
        for item in self.data_repo.list_macro_events(limit=limit):
            if symbol and item.get("affected_symbols") and symbol not in item["affected_symbols"]:
                continue
            events.append(_macro_item_to_market_event(item))
        return sorted(events, key=lambda item: item.occurred_at or datetime.min.replace(tzinfo=UTC), reverse=True)[
            :limit
        ]

    def build_feature_snapshot(self, *, symbol: str) -> MarketIntelligenceFeatureSnapshot:
        provider_status = self.provider_status()
        extras = self._latest_extras(symbol=symbol)
        active_events = self._active_blocking_events(symbol=symbol)
        evidence = []
        if extras is not None:
            evidence.append(f"market_extras:{symbol}:{extras.timestamp.isoformat()}")
        evidence.extend([f"risk_event:{event.risk_event_id}" for event in active_events if event.risk_event_id])
        component_scores = self._component_scores(symbol=symbol, extras=extras)
        active_cooldown = bool(active_events)
        return MarketIntelligenceFeatureSnapshot(
            symbol=symbol,
            generated_at=datetime.now(UTC),
            data_status=_snapshot_status(
                active_cooldown=active_cooldown,
                has_market_extras=extras is not None,
                has_component_scores=bool(component_scores),
            ),
            funding_rate=_decimal_to_float(extras.funding_rate) if extras and extras.funding_rate is not None else None,
            open_interest=(
                _decimal_to_float(extras.open_interest) if extras and extras.open_interest is not None else None
            ),
            long_ratio=_decimal_to_float(extras.long_ratio) if extras and extras.long_ratio is not None else None,
            short_ratio=_decimal_to_float(extras.short_ratio) if extras and extras.short_ratio is not None else None,
            liquidation_usd=_decimal_to_float(extras.liquidation_usd)
            if extras and extras.liquidation_usd is not None
            else None,
            news_risk_score=component_scores.get("news_risk", 0.0),
            macro_risk_score=component_scores.get("macro_risk", 0.0),
            active_event_cooldown=active_cooldown,
            cooldown_reason=_cooldown_reason(active_events),
            provider_status=provider_status,
            evidence_refs=evidence,
            component_scores=component_scores,
        )

    def build_signal(self, *, symbol: str) -> MarketIntelligenceSignal:
        snapshot = self.build_feature_snapshot(symbol=symbol)
        score = _aggregate_direction_score(snapshot.component_scores)
        long_probability = _clamp(0.5 + score / 2.0)
        short_probability = _clamp(1.0 - long_probability)
        confidence = min(abs(score) + _evidence_confidence(snapshot), 1.0)
        direction = TradeSide.LONG if score > 0.05 else TradeSide.SHORT if score < -0.05 else None
        risk_level = _risk_level(snapshot)
        participates = (
            settings.market_intelligence_enabled
            and not snapshot.active_event_cooldown
            and direction is not None
            and confidence >= 0.20
        )
        vote_weight = min(settings.market_intelligence_vote_weight_cap, 0.30) if participates else 0.0
        return MarketIntelligenceSignal(
            symbol=symbol,
            generated_at=datetime.now(UTC),
            long_probability=long_probability,
            short_probability=short_probability,
            confidence=confidence,
            direction=direction,
            risk_level=risk_level,
            vote_weight=vote_weight,
            component_scores=snapshot.component_scores,
            evidence_refs=snapshot.evidence_refs,
            rationale=_rationale(snapshot=snapshot, score=score, direction=direction),
            provider_status=snapshot.provider_status,
            active_event_cooldown=snapshot.active_event_cooldown,
            should_participate=participates,
        )

    def refresh(self, *, symbol: str = "BTC/USDT") -> dict[str, Any]:
        signal = self.build_signal(symbol=symbol)
        return {
            "symbol": symbol,
            "provider_status": signal.provider_status,
            "signal": signal.model_dump(mode="json"),
            "note": "provider adapters are non-fatal; missing CoinGlass/CryptoQuant credentials disable those inputs",
        }

    def _latest_extras(self, *, symbol: str) -> MarketExtras | None:
        return self.data_repo.get_latest_market_extras(symbol=symbol) or self.data_repo.get_latest_market_extras(
            symbol=f"{symbol}:USDT"
        )

    def _active_blocking_events(self, *, symbol: str) -> list[RiskEvent]:
        events: list[RiskEvent] = []
        for event in self.data_repo.list_risk_events(active_only=True):
            if event.severity not in {RiskSeverity.HIGH, RiskSeverity.CRITICAL}:
                continue
            if event.affected_scope is None or symbol in event.affected_scope:
                events.append(event)
        return events

    def _component_scores(self, *, symbol: str, extras: MarketExtras | None) -> dict[str, float]:
        scores: dict[str, float] = {}
        if extras is not None and extras.funding_rate is not None:
            funding = _decimal_to_float(extras.funding_rate)
            scores["funding_contrarian"] = _bounded_score(-funding, scale=0.001)
        if extras is not None and extras.long_ratio is not None and extras.short_ratio is not None:
            long_ratio = _decimal_to_float(extras.long_ratio)
            short_ratio = _decimal_to_float(extras.short_ratio)
            scores["long_short_contrarian"] = _bounded_score(short_ratio - long_ratio, scale=0.25)
        if extras is not None and extras.liquidation_usd is not None:
            liquidation = _decimal_to_float(extras.liquidation_usd)
            scores["liquidation_risk"] = -min(liquidation / 100_000_000.0, 1.0) * 0.30

        news_events = self.data_repo.list_news_items(limit=30)
        news_score = _news_direction_score(news_events, symbol=symbol)
        if news_score is not None:
            scores["news_direction"] = news_score
            scores["news_risk"] = min(abs(news_score), 1.0)

        macro_events = self.data_repo.list_macro_events(limit=30)
        macro_score = _macro_direction_score(macro_events, symbol=symbol)
        if macro_score is not None:
            scores["macro_direction"] = macro_score
            scores["macro_risk"] = min(abs(macro_score), 1.0)
        return scores


def _risk_event_to_market_event(event: RiskEvent) -> MarketEvent:
    return MarketEvent(
        event_id=f"risk:{event.risk_event_id or _digest(event.description)}",
        source=event.source,
        event_type="risk",
        occurred_at=event.occurred_at,
        title=event.description,
        importance=_severity_importance(event.severity),
        severity=event.severity,
        sentiment="bearish" if event.severity in {RiskSeverity.HIGH, RiskSeverity.CRITICAL} else "neutral",
        confidence=_severity_importance(event.severity),
        evidence_ref=f"risk_event:{event.risk_event_id}" if event.risk_event_id else None,
    )


def _news_item_to_market_event(item: dict[str, Any]) -> MarketEvent:
    sentiment = str(item.get("sentiment") or "neutral").lower()
    mapped: Literal["bullish", "bearish", "neutral"] = (
        "bullish" if sentiment == "positive" else "bearish" if sentiment == "negative" else "neutral"
    )
    severity = _severity(str(item.get("severity") or "low"))
    return MarketEvent(
        event_id=f"news:{item.get('id') or _digest(str(item))}",
        source=str(item.get("source") or "news"),
        event_type="news",
        occurred_at=_parse_time(item.get("published_at")),
        title=str(item.get("title") or "news item"),
        summary=item.get("summary"),
        importance=_severity_importance(severity),
        severity=severity,
        sentiment=mapped,
        confidence=0.65 if mapped != "neutral" else 0.35,
        evidence_ref=f"news_item:{item.get('id')}" if item.get("id") else None,
    )


def _macro_item_to_market_event(item: dict[str, Any]) -> MarketEvent:
    severity = _severity(str(item.get("impact") or "low"))
    return MarketEvent(
        event_id=f"macro:{item.get('id') or _digest(str(item))}",
        source=str(item.get("source") or "macro_calendar"),
        event_type="macro",
        occurred_at=_parse_time(item.get("scheduled_at")),
        title=str(item.get("event_name") or "macro event"),
        summary=item.get("notes"),
        importance=_severity_importance(severity),
        severity=severity,
        sentiment="bearish" if severity in {RiskSeverity.HIGH, RiskSeverity.CRITICAL} else "neutral",
        confidence=_severity_importance(severity),
        evidence_ref=f"macro_event:{item.get('id')}" if item.get("id") else None,
    )


def _news_direction_score(items: list[dict[str, Any]], *, symbol: str) -> float | None:
    values = []
    for item in items:
        affected = item.get("affected_symbols")
        if affected and symbol not in affected:
            continue
        sentiment = str(item.get("sentiment") or "").lower()
        severity = _severity(str(item.get("severity") or "low"))
        magnitude = _severity_importance(severity)
        if sentiment == "positive":
            values.append(magnitude)
        elif sentiment == "negative":
            values.append(-magnitude)
    return sum(values) / len(values) if values else None


def _macro_direction_score(items: list[dict[str, Any]], *, symbol: str) -> float | None:
    values = []
    for item in items:
        affected = item.get("affected_symbols")
        if affected and symbol not in affected:
            continue
        severity = _severity(str(item.get("impact") or "low"))
        if severity in {RiskSeverity.HIGH, RiskSeverity.CRITICAL}:
            values.append(-_severity_importance(severity))
    return sum(values) / len(values) if values else None


def _aggregate_direction_score(scores: dict[str, float]) -> float:
    if not scores:
        return 0.0
    weights = {
        "funding_contrarian": 0.25,
        "long_short_contrarian": 0.15,
        "liquidation_risk": 0.10,
        "news_direction": 0.20,
        "macro_direction": 0.15,
        "exchange_inflow_score": 0.10,
        "exchange_outflow_score": 0.10,
        "stablecoin_reserve_score": 0.10,
        "defi_growth_score": 0.05,
    }
    total_weight = sum(weights.get(key, 0.05) for key in scores)
    if total_weight <= 0:
        return 0.0
    weighted_sum = sum(_clamp(value, -1.0, 1.0) * weights.get(key, 0.05) for key, value in scores.items())
    return _clamp(weighted_sum / total_weight, -1.0, 1.0)


def _snapshot_status(
    *,
    active_cooldown: bool,
    has_market_extras: bool,
    has_component_scores: bool,
) -> Literal["ok", "partial", "empty", "cooldown"]:
    if active_cooldown:
        return "cooldown"
    if has_market_extras or has_component_scores:
        return "ok"
    return "partial"


def _evidence_confidence(snapshot: MarketIntelligenceFeatureSnapshot) -> float:
    provider_bonus = 0.05 * sum(1 for item in snapshot.provider_status.values() if item.get("status") == "ok")
    evidence_bonus = min(len(snapshot.evidence_refs) * 0.05, 0.20)
    return min(provider_bonus + evidence_bonus, 0.45)


def _risk_level(snapshot: MarketIntelligenceFeatureSnapshot) -> RiskLevel:
    if snapshot.active_event_cooldown or snapshot.macro_risk_score >= 0.8 or snapshot.news_risk_score >= 0.8:
        return RiskLevel.HIGH
    if snapshot.macro_risk_score >= 0.4 or snapshot.news_risk_score >= 0.4:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


def _rationale(
    *,
    snapshot: MarketIntelligenceFeatureSnapshot,
    score: float,
    direction: TradeSide | None,
) -> list[str]:
    if snapshot.active_event_cooldown:
        return [snapshot.cooldown_reason or "active high-severity event cooldown; vote disabled"]
    reasons = [f"aggregate intelligence score={score:.3f}"]
    if direction is not None:
        reasons.append(f"direction={direction}")
    for key, value in snapshot.component_scores.items():
        reasons.append(f"{key}={value:.3f}")
    return reasons[:8]


def _cooldown_reason(events: list[RiskEvent]) -> str | None:
    if not events:
        return None
    event = events[0]
    return f"{event.severity} {event.event_type}: {event.description}"


def _severity(value: str) -> RiskSeverity:
    normalized = value.lower().replace("medium", "mid")
    try:
        return RiskSeverity(normalized)
    except ValueError:
        return RiskSeverity.LOW


def _severity_importance(severity: RiskSeverity) -> float:
    return {
        RiskSeverity.LOW: 0.20,
        RiskSeverity.MID: 0.45,
        RiskSeverity.HIGH: 0.80,
        RiskSeverity.CRITICAL: 1.0,
    }[severity]


def _bounded_score(value: float, *, scale: float) -> float:
    if scale <= 0:
        return 0.0
    return _clamp(value / scale, -1.0, 1.0)


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(min(value, upper), lower)


def _decimal_to_float(value: Decimal) -> float:
    return float(value)


def _float_or_none(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _parse_time(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
        except ValueError:
            return None
    return None


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
