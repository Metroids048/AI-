"""API settings loaded from environment (.env / .env.example)."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    app_name: str = "ai-quant-research-platform"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    admin_api_token: str = "dev-admin-token"

    postgres_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/ai_quant"
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"
    runtime_scheduler_mode: str = "inprocess"
    runtime_scheduler_autostart: bool = True
    paper_runtime_cycle_seconds: int = 300
    market_data_heartbeat_seconds: int = 60
    market_data_stale_seconds: int = 120
    market_kline_stream_poll_seconds: int = 2
    notification_dispatch_seconds: int = 60
    daily_review_hour_utc: int = 0
    daily_review_minute_utc: int = 0

    claude_api_key: str = ""
    claude_model: str = "claude-sonnet-4-6"
    anthropic_api_base_url: str = "https://api.anthropic.com"
    agent_llm_provider_map: str = ""
    agent_llm_model_map: str = ""
    decision_veto_daily_budget: int = 200
    decision_veto_require_llm: bool = True

    binance_api_key: str = ""
    binance_api_secret: str = ""
    binance_use_testnet: bool = True
    live_trading_enabled: bool = False
    default_exchange: str = "binance"
    binance_live_universe_enabled: bool = False
    binance_live_market_enabled: bool = False
    binance_live_ws_enabled: bool = False
    binance_live_ws_symbols: str = "BTC/USDT"
    binance_live_ws_timeframe: str = "1m"

    trading_economics_api_key: str = ""
    alpha_vantage_api_key: str = ""
    forexfactory_rss_url: str = "https://forexfactory.com/calendar.rss"

    jinshi_rss_url: str = "https://rss.jin10.com/flash_newest.xml"
    coindesk_rss_url: str = "https://www.coindesk.com/arc/outboundfeeds/rss/"
    theblock_rss_url: str = "https://www.theblock.co/rss.xml"
    reuters_crypto_rss_url: str = ""
    sec_edgar_rss_url: str = "https://efts.sec.gov/LATEST/search-index?q=%22crypto%22"

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

    github_token: str = ""
    arxiv_categories: str = "q-fin.TR,q-fin.PM"
    worldquant_alpha_local_path: str = ""

    freqtrade_api_url: str = "http://freqtrade:8080"
    freqtrade_username: str = "freqtradeuser"
    freqtrade_password: str = ""


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
