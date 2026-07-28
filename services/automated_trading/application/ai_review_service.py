"""AI Review Service for V2 automated trading (plan section 12).

Two review types:
- MARKET_REVIEW: runs on a schedule even without a candidate. Validates API
  connectivity, summarises 4h/1h/15m features, emits market risk labels.
- TRADE_REVIEW: runs only when a candidate exists. Receives structured market
  features and outputs a fixed schema (bias / confidence / risk_flags / summary).

AI permissions (plan 12.2 / Gate 12):
- Advisory only; AI never creates candidates, never sets quantity/leverage,
  never writes absolute SL/TP, and never blocks a hard reduce-only exit.
- If the provider fails during Sampling, execution continues deterministically.
- Every invocation (or skip) produces an LLM invocation record.

Skip reasons (plan 12.3):
  API_KEY_MISSING, NO_CANDIDATE, MARKET_REVIEW_DISABLED, PROVIDER_ERROR,
  RATE_LIMITED, FORCED_EXIT_IN_PROGRESS.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)

try:
    import anthropic as anthropic
except ImportError:  # pragma: no cover
    anthropic = None


class AIReviewStage(StrEnum):
    MARKET_REVIEW = "MARKET_REVIEW"
    TRADE_REVIEW = "TRADE_REVIEW"


class AIReviewBias(StrEnum):
    SUPPORT = "support"
    NEUTRAL = "neutral"
    OPPOSE = "oppose"


class AIInvocationStatus(StrEnum):
    CALLED = "CALLED"
    SKIPPED = "SKIPPED"
    ERROR = "ERROR"


class AISkipReason(StrEnum):
    API_KEY_MISSING = "API_KEY_MISSING"
    NO_CANDIDATE = "NO_CANDIDATE"
    MARKET_REVIEW_DISABLED = "MARKET_REVIEW_DISABLED"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    RATE_LIMITED = "RATE_LIMITED"
    FORCED_EXIT_IN_PROGRESS = "FORCED_EXIT_IN_PROGRESS"


@dataclass(frozen=True)
class AIReviewResult:
    """Fixed-schema AI review output (plan section 12.1)."""

    stage: AIReviewStage
    status: AIInvocationStatus
    # Populated on successful CALLED reviews:
    bias: AIReviewBias | None = None
    confidence: Decimal | None = None
    risk_flags: tuple[str, ...] = ()
    summary: str = ""
    # Observability fields (always populated):
    skip_reason: AISkipReason | None = None
    provider: str | None = None
    model: str | None = None
    latency_ms: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    request_hash: str | None = None
    response_hash: str | None = None
    error_code: str | None = None
    invoked_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def advisory_veto(self) -> bool:
        """True only when AI explicitly opposes AND status is CALLED.

        Only CALLED oppose results carry advisory weight; SKIPPED / ERROR
        never veto an entry (plan 12.2).
        """
        return self.status is AIInvocationStatus.CALLED and self.bias is AIReviewBias.OPPOSE


@dataclass(frozen=True)
class MarketFeatures:
    """Structured market context passed to both review types."""

    symbol: str
    timeframe: str
    current_price: Decimal
    atr14: Decimal | None = None
    ema50: Decimal | None = None
    rsi14: Decimal | None = None
    macd_histogram: Decimal | None = None
    funding_rate: Decimal | None = None
    open_interest: Decimal | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def _hash_payload(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _build_market_prompt(features: MarketFeatures) -> str:
    return (
        f"Symbol: {features.symbol}\n"
        f"Timeframe: {features.timeframe}\n"
        f"Price: {features.current_price}\n"
        f"ATR14: {features.atr14}\n"
        f"EMA50: {features.ema50}\n"
        f"RSI14: {features.rsi14}\n"
        f"MACD Histogram: {features.macd_histogram}\n"
        "Summarise the current market regime and list any notable risk flags."
    )


def _build_trade_prompt(features: MarketFeatures, candidate_summary: dict[str, Any]) -> str:
    return (
        f"Symbol: {features.symbol}\n"
        f"Price: {features.current_price}\n"
        f"Candidate side: {candidate_summary.get('side')}\n"
        f"Confidence: {candidate_summary.get('confidence')}\n"
        f"Stop distance: {candidate_summary.get('stop_distance')}\n"
        f"TP distance: {candidate_summary.get('take_profit_distance')}\n"
        'Respond with JSON: {"bias": "support|neutral|oppose", '
        '"confidence": 0.0, "risk_flags": [], "summary": ""}'
    )


def _parse_trade_response(text: str) -> tuple[AIReviewBias, Decimal, tuple[str, ...], str]:
    """Parse LLM trade-review JSON into typed fields. Defaults to neutral on parse error."""
    try:
        data = json.loads(text)
        bias = AIReviewBias(data.get("bias", "neutral"))
        confidence = Decimal(str(data.get("confidence", 0.0)))
        risk_flags = tuple(str(f) for f in data.get("risk_flags", []))
        summary = str(data.get("summary", ""))
        return bias, confidence, risk_flags, summary
    except Exception:  # noqa: BLE001
        return AIReviewBias.NEUTRAL, Decimal("0"), (), "parse error — defaulted to neutral"


def run_market_review(
    features: MarketFeatures,
    *,
    api_key: str | None = None,
    provider: str = "anthropic",
    model: str = "claude-haiku-4-5-20251001",
    market_review_enabled: bool = True,
) -> AIReviewResult:
    """Run a MARKET_REVIEW LLM call or return a SKIPPED record.

    This always runs, even without a candidate, to validate API connectivity
    and produce the market-state label (plan 12.1).

    Args:
        features: Structured market features for the prompt.
        api_key: Provider API key. None -> API_KEY_MISSING skip.
        provider: Provider name for the invocation record.
        model: Model identifier.
        market_review_enabled: If False -> MARKET_REVIEW_DISABLED skip.
    """
    stage = AIReviewStage.MARKET_REVIEW

    if not market_review_enabled:
        return AIReviewResult(
            stage=stage,
            status=AIInvocationStatus.SKIPPED,
            skip_reason=AISkipReason.MARKET_REVIEW_DISABLED,
        )

    if not api_key:
        return AIReviewResult(
            stage=stage,
            status=AIInvocationStatus.SKIPPED,
            skip_reason=AISkipReason.API_KEY_MISSING,
        )

    prompt = _build_market_prompt(features)
    request_hash = _hash_payload({"prompt": prompt, "model": model})
    t0 = datetime.now(UTC)

    try:
        if anthropic is None:
            raise ImportError("anthropic package not installed")
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model=model,
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        latency_ms = int((datetime.now(UTC) - t0).total_seconds() * 1000)
        content = msg.content[0].text if msg.content else ""
        response_hash = _hash_payload({"response": content})

        return AIReviewResult(
            stage=stage,
            status=AIInvocationStatus.CALLED,
            bias=AIReviewBias.NEUTRAL,
            confidence=Decimal("0"),
            summary=content[:500],
            provider=provider,
            model=model,
            latency_ms=latency_ms,
            prompt_tokens=msg.usage.input_tokens,
            completion_tokens=msg.usage.output_tokens,
            total_tokens=msg.usage.input_tokens + msg.usage.output_tokens,
            request_hash=request_hash,
            response_hash=response_hash,
        )

    except Exception as exc:  # noqa: BLE001
        latency_ms = int((datetime.now(UTC) - t0).total_seconds() * 1000)
        error_code = type(exc).__name__
        logger.warning("[ai_review] MARKET_REVIEW error: %s: %s", error_code, exc)
        return AIReviewResult(
            stage=stage,
            status=AIInvocationStatus.ERROR,
            skip_reason=AISkipReason.PROVIDER_ERROR,
            provider=provider,
            model=model,
            latency_ms=latency_ms,
            request_hash=request_hash,
            error_code=error_code,
        )


def run_trade_review(
    features: MarketFeatures,
    candidate_summary: dict[str, Any],
    *,
    api_key: str | None = None,
    provider: str = "anthropic",
    model: str = "claude-haiku-4-5-20251001",
    forced_exit_in_progress: bool = False,
) -> AIReviewResult:
    """Run a TRADE_REVIEW LLM call for a specific candidate.

    Always returns a result — provider failures yield ERROR status and never
    block Sampling execution (plan 12.2).

    Args:
        features: Market context.
        candidate_summary: Serialisable subset of TradeCandidate fields.
        api_key: Provider API key. None -> API_KEY_MISSING skip.
        provider: Provider name.
        model: Model identifier.
        forced_exit_in_progress: If True -> FORCED_EXIT_IN_PROGRESS skip.
    """
    stage = AIReviewStage.TRADE_REVIEW

    if forced_exit_in_progress:
        return AIReviewResult(
            stage=stage,
            status=AIInvocationStatus.SKIPPED,
            skip_reason=AISkipReason.FORCED_EXIT_IN_PROGRESS,
        )

    if not api_key:
        return AIReviewResult(
            stage=stage,
            status=AIInvocationStatus.SKIPPED,
            skip_reason=AISkipReason.API_KEY_MISSING,
        )

    prompt = _build_trade_prompt(features, candidate_summary)
    request_hash = _hash_payload({"prompt": prompt, "model": model})
    t0 = datetime.now(UTC)

    try:
        if anthropic is None:
            raise ImportError("anthropic package not installed")
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model=model,
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        latency_ms = int((datetime.now(UTC) - t0).total_seconds() * 1000)
        content = msg.content[0].text if msg.content else ""
        response_hash = _hash_payload({"response": content})

        bias, confidence, risk_flags, summary = _parse_trade_response(content)

        return AIReviewResult(
            stage=stage,
            status=AIInvocationStatus.CALLED,
            bias=bias,
            confidence=confidence,
            risk_flags=risk_flags,
            summary=summary,
            provider=provider,
            model=model,
            latency_ms=latency_ms,
            prompt_tokens=msg.usage.input_tokens,
            completion_tokens=msg.usage.output_tokens,
            total_tokens=msg.usage.input_tokens + msg.usage.output_tokens,
            request_hash=request_hash,
            response_hash=response_hash,
        )

    except Exception as exc:  # noqa: BLE001
        latency_ms = int((datetime.now(UTC) - t0).total_seconds() * 1000)
        error_code = type(exc).__name__
        logger.warning("[ai_review] TRADE_REVIEW error: %s: %s", error_code, exc)
        # Provider failures NEVER block sampling execution (plan 12.2).
        return AIReviewResult(
            stage=stage,
            status=AIInvocationStatus.ERROR,
            skip_reason=AISkipReason.PROVIDER_ERROR,
            provider=provider,
            model=model,
            latency_ms=latency_ms,
            request_hash=request_hash,
            error_code=error_code,
        )
