-- TimescaleDB initialization (mounted at /docker-entrypoint-initdb.d/).
-- Idempotent: safe to re-run.
--
-- STORAGE-OWNERSHIP CONTRACT (see docs/architecture/v2-integration-reconciliation.md):
--   * This file owns TIME-SERIES and EVENT tables ONLY:
--       ohlcv_bars, market_extras, risk_events, macro_events.
--   * Alembic owns RELATIONAL tables (strategies, versions, runs, ...).
--   No table may be created by both. Do NOT add `strategies` here.
--
-- Order Book data is intentionally absent: it lives only in Redis (latest
-- snapshot), never persisted here (PDF §3.2 / risk table 6.2).

CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS pgcrypto;  -- for gen_random_uuid()

-- A-level: OHLCV candles ------------------------------------------------------
CREATE TABLE IF NOT EXISTS ohlcv_bars (
    time       TIMESTAMPTZ   NOT NULL,
    symbol     VARCHAR(20)   NOT NULL,   -- BTC/USDT
    exchange   VARCHAR(20)   NOT NULL,   -- binance
    timeframe  VARCHAR(10)   NOT NULL,   -- 1h
    open       NUMERIC,
    high       NUMERIC,
    low        NUMERIC,
    close      NUMERIC,
    volume     NUMERIC
);
SELECT create_hypertable('ohlcv_bars', 'time', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS idx_ohlcv_symbol_tf_time
    ON ohlcv_bars (symbol, timeframe, time DESC);
CREATE UNIQUE INDEX IF NOT EXISTS uq_ohlcv_symbol_exchange_tf_time
    ON ohlcv_bars (symbol, exchange, timeframe, time);

-- A-level: crypto extras (funding / OI / long-short / liquidation) ------------
CREATE TABLE IF NOT EXISTS market_extras (
    time            TIMESTAMPTZ NOT NULL,
    symbol          VARCHAR(20) NOT NULL,
    funding_rate    NUMERIC,
    open_interest   NUMERIC,
    long_ratio      NUMERIC,
    short_ratio     NUMERIC,
    liquidation_usd NUMERIC
);
SELECT create_hypertable('market_extras', 'time', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS idx_market_extras_symbol_time
    ON market_extras (symbol, time DESC);
CREATE UNIQUE INDEX IF NOT EXISTS uq_market_extras_symbol_time
    ON market_extras (symbol, time);

-- C/D-level: risk events (subset of shared.models.RiskEvent superset) ---------
CREATE TABLE IF NOT EXISTS risk_events (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at       TIMESTAMPTZ DEFAULT NOW(),
    source           VARCHAR(50),     -- jinshi / twitter / macro_calendar
    level            VARCHAR(20),     -- low / mid / high / critical (== severity)
    event_type       VARCHAR(40),     -- macro_event / news_risk / ...
    description      TEXT,
    affected_symbols TEXT[],          -- NULL == whole market
    expires_at       TIMESTAMPTZ,
    resolution_status VARCHAR(30) DEFAULT 'detected'
);
CREATE INDEX IF NOT EXISTS idx_risk_events_created ON risk_events (created_at DESC);

-- B-level: macro economic calendar --------------------------------------------
CREATE TABLE IF NOT EXISTS macro_events (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_name       VARCHAR(80) NOT NULL,
    source           VARCHAR(50),
    impact           VARCHAR(20),     -- low / mid / high / critical
    scheduled_at     TIMESTAMPTZ NOT NULL,
    affected_symbols TEXT[],
    notes            TEXT
);
CREATE INDEX IF NOT EXISTS idx_macro_events_scheduled ON macro_events (scheduled_at);
