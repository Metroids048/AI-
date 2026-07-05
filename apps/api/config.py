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

    claude_api_key: str = ""
    claude_model: str = "claude-sonnet-4-6"
    anthropic_api_base_url: str = "https://api.anthropic.com"
    agent_llm_provider_map: str = ""
    agent_llm_model_map: str = ""

    binance_api_key: str = ""
    binance_api_secret: str = ""
    binance_use_testnet: bool = True
    live_trading_enabled: bool = False
    okx_api_key: str = ""
    okx_api_secret: str = ""
    okx_passphrase: str = ""
    bybit_api_key: str = ""
    bybit_api_secret: str = ""
    default_exchange: str = "binance"

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

    github_token: str = ""
    arxiv_categories: str = "q-fin.TR,q-fin.PM"
    worldquant_alpha_local_path: str = ""

    freqtrade_api_url: str = "http://freqtrade:8080"
    freqtrade_username: str = "freqtradeuser"
    freqtrade_password: str = ""


settings = Settings()
