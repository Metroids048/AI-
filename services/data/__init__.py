"""Data Layer public API.

The data package owns market-data normalization, persisted timeseries access,
and ingestion-job preparation. Service layers import from here instead of
reaching into implementation modules when possible.
"""

from __future__ import annotations

from .binance import (
    BinanceBackfillResult,
    BinanceBackfillService,
    BinanceCcxtClient,
    BinanceLiveMarketCollector,
    BinanceUniverseSelector,
    normalize_funding_rate_history,
    normalize_ohlcv_rows,
    normalize_ws_kline_event,
    normalize_ws_mark_price_event,
    spot_to_usdm_perp_symbol,
    stream_symbol,
)
from .capabilities import list_exchange_capabilities
from .heartbeat import MarketDataHeartbeatService
from .live_feed_bus import LiveFeedBus, live_feed_bus
from .market import MarketQueryService
from .market_intelligence import (
    CoinGlassProvider,
    CryptoQuantProvider,
    DeFiLlamaProvider,
    MarketIntelligenceService,
)
from .repository import TIMESERIES_METADATA, DataRepository, create_timeseries_schema
from .service import DEFAULT_BINANCE_TOP20, IngestionService

__all__ = [
    "BinanceUniverseSelector",
    "BinanceBackfillResult",
    "BinanceBackfillService",
    "BinanceCcxtClient",
    "BinanceLiveMarketCollector",
    "DataRepository",
    "DEFAULT_BINANCE_TOP20",
    "IngestionService",
    "LiveFeedBus",
    "live_feed_bus",
    "list_exchange_capabilities",
    "MarketQueryService",
    "MarketDataHeartbeatService",
    "MarketIntelligenceService",
    "CoinGlassProvider",
    "CryptoQuantProvider",
    "DeFiLlamaProvider",
    "TIMESERIES_METADATA",
    "create_timeseries_schema",
    "normalize_funding_rate_history",
    "normalize_ohlcv_rows",
    "normalize_ws_kline_event",
    "normalize_ws_mark_price_event",
    "spot_to_usdm_perp_symbol",
    "stream_symbol",
]
