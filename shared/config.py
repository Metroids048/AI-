"""Canonical application settings.

This is the single source of truth for runtime configuration. It lives in
`shared/` so that every layer (`apps/`, `services/`, `research_source/`) can
import it without creating a reverse dependency back into `apps/api`.

Previously `Settings` lived in `apps/api/config.py` and `services/*` imported
it from there — a layering violation (services must not depend on apps).
`apps/api/config.py` now re-exports from here for backward compatibility.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ---- Application ----
    app_env: str = "development"
    app_name: str = "ai-quant-research-platform"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    admin_api_token: str = "dev-admin-token"
    cors_allowed_origins: str = ""  # comma-separated whitelist; empty = deny all

    # ---- Infrastructure ----
    postgres_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/ai_quant"
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"
    # Database connection pool (previously unconfigured → default pool_size=5)
    db_pool_size: int = 20
    db_pool_recycle_seconds: int = 1800
    db_pool_pre_ping: bool = True
    # Celery worker tuning (previously unconfigured)
    celery_worker_concurrency: int = 4
    celery_task_time_limit_seconds: int = 600
    celery_task_soft_time_limit_seconds: int = 540
    celery_worker_prefetch_count: int = 1

    # ---- Runtime scheduler ----
    runtime_scheduler_mode: str = "inprocess"
    runtime_scheduler_autostart: bool = True
    paper_runtime_cycle_seconds: int = 300
    paper_runtime_enable_decision_veto: bool = True
    # Local dev: bypass multi-timeframe + meta-label filters so Paper cycles can open test positions.
    paper_runtime_relaxed_signals: bool = False
    # When true, auto-cycle submits to Binance before local paper fill (same path as manual testnet).
    binance_auto_execute: bool = True
    market_data_heartbeat_seconds: int = 60
    market_data_stale_seconds: int = 120
    # Gatekeeper order-freshness threshold (previously hardcoded to 2h).
    execution_freshness_delay_seconds: int = 7200
    market_kline_stream_poll_seconds: int = 2
    notification_dispatch_seconds: int = 60
    daily_review_hour_utc: int = 0
    daily_review_minute_utc: int = 0

    # ---- LLM ----
    claude_api_key: str = ""
    claude_model: str = "claude-sonnet-4-6"
    anthropic_api_base_url: str = "https://api.anthropic.com"
    agent_llm_provider_map: str = ""
    agent_llm_model_map: str = ""
    openrouter_api_key: str = ""
    openrouter_free_models: str = ""
    github_models_token: str = ""
    github_models_free_models: str = ""
    llm_free_model_catalog_cache_seconds: int = 21600
    decision_veto_daily_budget: int = 200
    decision_veto_require_llm: bool = True

    # ---- Exchange / trading ----
    binance_api_key: str = ""
    binance_api_secret: str = ""
    binance_use_testnet: bool = True
    # demo = Mock Trading (demo.binance.com, unified web entry)
    # testnet = legacy fapi host only (auto-fallback when demo API geo-blocked)
    binance_trading_mode: str = "demo"
    live_trading_enabled: bool = False
    default_exchange: str = "binance"
    binance_live_universe_enabled: bool = True
    binance_live_market_enabled: bool = True
    binance_live_ws_enabled: bool = True
    binance_live_ws_symbols: str = "top20"
    binance_live_ws_timeframe: str = "1m"
    # App-scoped proxy for Binance only (e.g. http://127.0.0.1:7890). Avoids global VPN.
    binance_http_proxy: str = ""
    binance_https_proxy: str = ""
    # Public market-data endpoints (override for geo-restricted regions).
    binance_spot_rest_base: str = "https://api.binance.com"
    binance_usdm_rest_base: str = "https://demo-fapi.binance.com"
    binance_spot_ws_base: str = "wss://stream.binance.com:9443/ws"
    binance_usdm_ws_base: str = "wss://fstream.binance.com/ws"
    # Kill switch — global trading halt via Redis flag
    kill_switch_enabled: bool = True
    kill_switch_redis_key: str = "ai_quant:kill_switch"

    # ---- Macro / news data sources ----
    trading_economics_api_key: str = ""
    alpha_vantage_api_key: str = ""
    forexfactory_rss_url: str = "https://forexfactory.com/calendar.rss"
    jinshi_rss_url: str = "https://rss.jin10.com/flash_newest.xml"
    coindesk_rss_url: str = "https://www.coindesk.com/arc/outboundfeeds/rss/"
    theblock_rss_url: str = "https://www.theblock.co/rss.xml"
    reuters_crypto_rss_url: str = ""
    sec_edgar_rss_url: str = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=&company=&dateb=&owner=include&start=0&count=40&output=atom&q=%22crypto%22"

    # ---- Social / notifications ----
    twitter_bearer_token: str = ""
    twitter_watch_user_ids: str = "25073877,44196397,902926941413453824"
    telegram_bot_token: str = ""
    telegram_channel_ids: str = ""
    notification_webhook_url: str = ""
    notification_dispatch_max_attempts: int = 3
    notification_dispatch_base_delay_seconds: int = 300
    news_high_severity_pause_minutes: int = 30
    macro_event_pause_before_minutes: int = 30
    macro_event_pause_after_minutes: int = 15

    # ---- Research sources ----
    github_token: str = ""
    arxiv_categories: str = "q-fin.TR,q-fin.PM"
    worldquant_alpha_local_path: str = ""

    # ---- Freqtrade (declared but not yet wired) ----
    freqtrade_api_url: str = "http://freqtrade:8080"
    freqtrade_username: str = "freqtradeuser"
    freqtrade_password: str = ""

    # ---- Validation admission thresholds (canonical, replaces hardcoded copies) ----
    validation_min_sharpe: float = 1.0
    validation_min_profit_factor: float = 1.3
    validation_max_drawdown: float = 0.25
    validation_min_expectancy: float = 0.0


def validate_trading_environment(config: Settings) -> None:
    """Fail closed for environments that must not touch mainnet trading."""

    guarded_envs = {"paper", "testnet"}
    app_env = config.app_env.lower()
    if app_env in guarded_envs and not config.binance_use_testnet:
        raise ValueError("paper/testnet environments require BINANCE_USE_TESTNET=true")
    if app_env in guarded_envs and config.live_trading_enabled:
        raise ValueError("paper/testnet environments require LIVE_TRADING_ENABLED=false")
    if app_env not in {"development", "test"} and config.admin_api_token == "dev-admin-token":
        raise ValueError("non-local environments require a non-default ADMIN_API_TOKEN")


settings = Settings()
validate_trading_environment(settings)
