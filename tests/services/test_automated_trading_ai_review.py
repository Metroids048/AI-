"""Task 12: AI Review Service tests (plan section 12 / Gate 12).

Gate 12:
- MARKET_REVIEW runs without a candidate.
- Every call or skip produces an invocation record.
- Provider failure during Sampling keeps execution deterministic (no veto).
- Forced exit never invokes AI.
- advisory_veto is True only for CALLED+oppose.
- Token usage is captured when available.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch

from services.automated_trading.application.ai_review_service import (
    AIInvocationStatus,
    AIReviewBias,
    AIReviewStage,
    AISkipReason,
    MarketFeatures,
    run_market_review,
    run_trade_review,
)


def _features() -> MarketFeatures:
    return MarketFeatures(
        symbol="BTC/USDT",
        timeframe="15m",
        current_price=Decimal("101000"),
        atr14=Decimal("350"),
        ema50=Decimal("99000"),
        rsi14=Decimal("56"),
        macd_histogram=Decimal("12"),
    )


def _candidate_summary() -> dict:
    return {
        "side": "LONG",
        "confidence": "0.55",
        "stop_distance": "420",
        "take_profit_distance": "630",
    }


# ---------------------------------------------------------------------------
# run_market_review
# ---------------------------------------------------------------------------


class TestRunMarketReview:
    def test_skips_when_api_key_missing(self) -> None:
        result = run_market_review(_features(), api_key=None)

        assert result.stage == AIReviewStage.MARKET_REVIEW
        assert result.status == AIInvocationStatus.SKIPPED
        assert result.skip_reason == AISkipReason.API_KEY_MISSING

    def test_skips_when_market_review_disabled(self) -> None:
        result = run_market_review(_features(), api_key="key", market_review_enabled=False)

        assert result.status == AIInvocationStatus.SKIPPED
        assert result.skip_reason == AISkipReason.MARKET_REVIEW_DISABLED

    def test_skip_has_invocation_record(self) -> None:
        result = run_market_review(_features(), api_key=None)

        # Always has stage and status fields.
        assert result.stage is not None
        assert result.status is not None
        assert result.invoked_at is not None

    def test_provider_error_produces_error_record(self) -> None:
        with patch("services.automated_trading.application.ai_review_service.anthropic") as mock_anthropic:
            mock_anthropic.Anthropic.return_value.messages.create.side_effect = RuntimeError("network error")
            result = run_market_review(_features(), api_key="fake-key")

        assert result.status == AIInvocationStatus.ERROR
        assert result.skip_reason == AISkipReason.PROVIDER_ERROR
        assert result.error_code == "RuntimeError"

    def test_called_result_has_provider_and_model(self) -> None:
        mock_msg = MagicMock()
        mock_msg.content = [MagicMock(text="Market is trending up")]
        mock_msg.usage.input_tokens = 50
        mock_msg.usage.output_tokens = 30

        with patch("services.automated_trading.application.ai_review_service.anthropic") as mock_anthropic:
            mock_anthropic.Anthropic.return_value.messages.create.return_value = mock_msg
            result = run_market_review(
                _features(), api_key="key", provider="anthropic", model="claude-haiku-4-5-20251001"
            )

        assert result.status == AIInvocationStatus.CALLED
        assert result.provider == "anthropic"
        assert result.model == "claude-haiku-4-5-20251001"
        assert result.prompt_tokens == 50
        assert result.completion_tokens == 30
        assert result.total_tokens == 80

    def test_called_result_has_request_and_response_hash(self) -> None:
        mock_msg = MagicMock()
        mock_msg.content = [MagicMock(text="OK")]
        mock_msg.usage.input_tokens = 10
        mock_msg.usage.output_tokens = 5

        with patch("services.automated_trading.application.ai_review_service.anthropic") as mock_anthropic:
            mock_anthropic.Anthropic.return_value.messages.create.return_value = mock_msg
            result = run_market_review(_features(), api_key="key")

        assert result.request_hash is not None
        assert result.response_hash is not None


# ---------------------------------------------------------------------------
# run_trade_review
# ---------------------------------------------------------------------------


class TestRunTradeReview:
    def test_skips_when_api_key_missing(self) -> None:
        result = run_trade_review(_features(), _candidate_summary(), api_key=None)

        assert result.stage == AIReviewStage.TRADE_REVIEW
        assert result.status == AIInvocationStatus.SKIPPED
        assert result.skip_reason == AISkipReason.API_KEY_MISSING

    def test_skips_for_forced_exit(self) -> None:
        result = run_trade_review(_features(), _candidate_summary(), api_key="key", forced_exit_in_progress=True)

        assert result.status == AIInvocationStatus.SKIPPED
        assert result.skip_reason == AISkipReason.FORCED_EXIT_IN_PROGRESS

    def test_provider_failure_does_not_block_sampling(self) -> None:
        """Provider failure must return ERROR, never raise (plan 12.2)."""
        with patch("services.automated_trading.application.ai_review_service.anthropic") as mock_anthropic:
            mock_anthropic.Anthropic.return_value.messages.create.side_effect = ConnectionError("timeout")
            result = run_trade_review(_features(), _candidate_summary(), api_key="key")

        # Must not raise; must return a usable result.
        assert result.status == AIInvocationStatus.ERROR
        assert result.advisory_veto is False  # errors never veto

    def test_oppose_bias_sets_advisory_veto(self) -> None:
        mock_msg = MagicMock()
        mock_msg.content = [
            MagicMock(text='{"bias": "oppose", "confidence": 0.8, "risk_flags": ["high_vol"], "summary": "risky"}')
        ]
        mock_msg.usage.input_tokens = 20
        mock_msg.usage.output_tokens = 30

        with patch("services.automated_trading.application.ai_review_service.anthropic") as mock_anthropic:
            mock_anthropic.Anthropic.return_value.messages.create.return_value = mock_msg
            result = run_trade_review(_features(), _candidate_summary(), api_key="key")

        assert result.status == AIInvocationStatus.CALLED
        assert result.bias == AIReviewBias.OPPOSE
        assert result.advisory_veto is True

    def test_support_bias_does_not_veto(self) -> None:
        mock_msg = MagicMock()
        mock_msg.content = [MagicMock(text='{"bias": "support", "confidence": 0.7, "risk_flags": [], "summary": "ok"}')]
        mock_msg.usage.input_tokens = 20
        mock_msg.usage.output_tokens = 20

        with patch("services.automated_trading.application.ai_review_service.anthropic") as mock_anthropic:
            mock_anthropic.Anthropic.return_value.messages.create.return_value = mock_msg
            result = run_trade_review(_features(), _candidate_summary(), api_key="key")

        assert result.advisory_veto is False
        assert result.bias == AIReviewBias.SUPPORT

    def test_skipped_result_never_vetoes(self) -> None:
        result = run_trade_review(_features(), _candidate_summary(), api_key=None)
        assert result.advisory_veto is False

    def test_every_invocation_has_record(self) -> None:
        """Even a skipped review has a complete invocation record."""
        result = run_trade_review(_features(), _candidate_summary(), api_key=None)

        assert result.stage == AIReviewStage.TRADE_REVIEW
        assert result.status is not None
        assert result.invoked_at is not None

    def test_token_usage_captured(self) -> None:
        mock_msg = MagicMock()
        mock_msg.content = [MagicMock(text='{"bias": "neutral", "confidence": 0.5, "risk_flags": [], "summary": "ok"}')]
        mock_msg.usage.input_tokens = 100
        mock_msg.usage.output_tokens = 50

        with patch("services.automated_trading.application.ai_review_service.anthropic") as mock_anthropic:
            mock_anthropic.Anthropic.return_value.messages.create.return_value = mock_msg
            result = run_trade_review(_features(), _candidate_summary(), api_key="key")

        assert result.prompt_tokens == 100
        assert result.completion_tokens == 50
        assert result.total_tokens == 150

    def test_parse_error_defaults_to_neutral(self) -> None:
        mock_msg = MagicMock()
        mock_msg.content = [MagicMock(text="This is not JSON")]
        mock_msg.usage.input_tokens = 10
        mock_msg.usage.output_tokens = 5

        with patch("services.automated_trading.application.ai_review_service.anthropic") as mock_anthropic:
            mock_anthropic.Anthropic.return_value.messages.create.return_value = mock_msg
            result = run_trade_review(_features(), _candidate_summary(), api_key="key")

        assert result.status == AIInvocationStatus.CALLED
        assert result.bias == AIReviewBias.NEUTRAL
        assert result.advisory_veto is False
