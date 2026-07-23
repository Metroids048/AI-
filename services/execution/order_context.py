"""Shared exchange execution context for manual and automatic Paper orders."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from services.execution.order_normalizer import CcxtMarketRulesLoader, OrderNormalizationError
from shared.models import ExecutionOrderRequest, MarketRulesSnapshot, PaperRun


class MarketRulesUnavailable(OrderNormalizationError):
    """Raised when current exchange metadata cannot prove an order executable."""


class OrderExecutionContextBuilder:
    def __init__(self, gateway: Any) -> None:
        self.gateway = gateway

    def build(
        self,
        request: ExecutionOrderRequest,
        *,
        paper_run: PaperRun | None = None,
        order_origin: str,
        require_market_rules: bool = True,
    ) -> ExecutionOrderRequest:
        leverage = Decimal(str(request.entry_context.get("requested_leverage") or 1))
        if not require_market_rules:
            return request.model_copy(
                update={
                    "entry_context": {
                        **request.entry_context,
                        "exchange_account": self._exchange_account(),
                    },
                    "order_origin": order_origin,
                }
            )
        provider = getattr(self.gateway, "load_market_rules_snapshot", None)
        try:
            if callable(provider):
                rules = provider(symbol=request.symbol, leverage=leverage, loaded_at=datetime.now(UTC))
            else:
                client = getattr(self.gateway, "client", None)
                if client is None:
                    raise MarketRulesUnavailable("MARKET_RULES_UNAVAILABLE: gateway has no market metadata provider")
                rules = CcxtMarketRulesLoader(client).load(
                    symbol=request.symbol,
                    position_mode=self._position_mode(client),
                    margin_mode=self._margin_mode(client, request.symbol),
                    leverage=leverage,
                    loaded_at=datetime.now(UTC),
                )
        except Exception as exc:  # noqa: BLE001 - exchange metadata failures share one fail-closed contract
            if isinstance(exc, MarketRulesUnavailable):
                raise
            raise MarketRulesUnavailable(f"MARKET_RULES_UNAVAILABLE: {exc}") from exc

        profile = dict(paper_run.execution_profile) if paper_run is not None else {}
        context = {
            **request.entry_context,
            "fixed_position_settings": {
                key: profile.get(key)
                for key in ("risk_per_trade", "max_position_fraction", "max_total_exposure")
                if profile.get(key) is not None
            },
            "fixed_leverage_settings": {
                "requested_leverage": float(leverage),
                "max_leverage": profile.get("max_leverage"),
            },
            "position_mode": rules.position_mode,
            "market_rules_snapshot_id": rules.rules_snapshot_id,
            "exchange_account": self._exchange_account(),
        }
        return request.model_copy(
            update={
                "entry_context": context,
                "market_rules_snapshot": rules,
                "order_origin": order_origin,
            }
        )

    def _exchange_account(self) -> str:
        capability = getattr(self.gateway, "capability", None)
        exchange = str(getattr(capability, "exchange", None) or "paper")
        market_type = str(getattr(capability, "market_type", None) or "paper")
        api_backend = str(getattr(self.gateway, "api_backend", None) or "local")
        return f"{exchange}:{market_type}:{api_backend}"

    @staticmethod
    def _position_mode(client: Any) -> str:
        fetch = getattr(client, "fetch_position_mode", None)
        if callable(fetch):
            result = fetch()
            if isinstance(result, dict) and "hedged" in result:
                return "HEDGE" if bool(result["hedged"]) else "ONE_WAY"
        raw = getattr(client, "fapiPrivateGetPositionSideDual", None)
        if callable(raw):
            result = raw()
            if isinstance(result, dict) and "dualSidePosition" in result:
                return "HEDGE" if str(result["dualSidePosition"]).lower() == "true" else "ONE_WAY"
        raise MarketRulesUnavailable("exchange position mode is unavailable")

    @staticmethod
    def _margin_mode(client: Any, symbol: str) -> str:
        options = getattr(client, "options", {})
        if isinstance(options, dict):
            configured = str(options.get("defaultMarginMode") or "").upper()
            if configured in {"CROSS", "ISOLATED"}:
                return configured
        fetch = getattr(client, "fetch_positions", None)
        if callable(fetch):
            for position in fetch([symbol]):
                if not isinstance(position, dict):
                    continue
                raw_info = position.get("info")
                info: dict[str, Any] = raw_info if isinstance(raw_info, dict) else {}
                isolated = position.get("isolated", info.get("isolated"))
                if isolated is not None:
                    return "ISOLATED" if str(isolated).lower() == "true" else "CROSS"
        raise MarketRulesUnavailable("exchange margin mode is unavailable")


def build_gateway_market_rules(
    *,
    client: Any,
    symbol: str,
    exchange_symbol: str | None = None,
    leverage: Decimal,
    loaded_at: datetime,
) -> MarketRulesSnapshot:
    return CcxtMarketRulesLoader(client).load(
        symbol=symbol,
        exchange_symbol=exchange_symbol,
        position_mode=OrderExecutionContextBuilder._position_mode(client),
        margin_mode=OrderExecutionContextBuilder._margin_mode(client, exchange_symbol or symbol),
        leverage=leverage,
        loaded_at=loaded_at,
    )
