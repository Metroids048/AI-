# Project Memory

## Real-time trading console + multi-screen split (TASK-031, 2026-07-07)

- The Paper trading console's "not real-time" complaint had a concrete root cause chain, not a missing feature: `binance_live_universe_enabled` / `binance_live_market_enabled` / `binance_live_ws_enabled` in `apps/api/config.py` defaulted to `False`, so `/market/exchange-stream` was silently degrading to 2s REST polling dressed up as WS frames. All three now default `True`; `tests/conftest.py` gained an `autouse` fixture that forces them back to `False` during tests so the suite never makes real outbound Binance calls.
- `frontend/admin/src/hooks/useConsoleData.js` no longer bumps `candleSnapshotVersion` (full chart rebuild) on every 8s poll tick while the WS stream is `live` — only when genuinely falling back to REST. Live klines now flow purely through the WS `kline` event's incremental `update()`. The WS connection also now reconnects with exponential backoff (capped at 15s) on `onclose`/`onerror` instead of going permanently idle until the user changes symbol/timeframe.
- Trading page layout (`PaperConsole.jsx` + `styles.css`): the order ticket (`.ticket-rail`) is no longer buried at the bottom of a single-column stack below 1280px — CSS `order` now puts chart → ticket → market list → order book, so the buy/sell form is reachable within one screen at common widths.
- Non-core-trading content was moved out of the trading page into the routes already scaffolded for it: `RiskEventFeed` → `/risk` (`RiskConsole.jsx`, replacing its hand-written table, now with a real resolve/acknowledge action wired to `PATCH /risk/events/{id}/resolution`), news/macro/notifications → `/ops` (`OpsConsole.jsx`, previously a placeholder), review reports → `/review` (`ReviewCenter.jsx`, previously a placeholder). Both new pages use `@tanstack/react-query` `useQuery`, matching `RiskConsole.jsx`'s existing pattern rather than `useConsoleData.js`'s hand-rolled fetch/poll style, since they have no legacy coupling to that hook.
- Deliberate deviation from the literal plan text: `RuntimeControlPanel` and `DecisionDebugPanel` stayed on `/trading` (inside a new always-visible `execution-grid`, no longer hidden behind an accordion) instead of moving to `/ops`, because both are scoped to the currently-selected symbol/timeframe, not global ops state — moving them would have separated a trader's control from the chart they're commenting on.
- `OpsPanels.jsx`'s `FeedPanel` is now exported and reused directly by `OpsConsole.jsx`/`ReviewCenter.jsx`; the old `OpsReviewPanel` wrapper (and the now-dead `newsItems`/`macroEvents`/`reviews`/`notifications` fetches + state fields in `useConsoleData.js`) were deleted once Grep confirmed zero remaining callers, removing four redundant REST calls from the trading page's 8-second poll cycle.
- Verification: `py -3 -m pytest tests/ -q` -> 151 passed, 1 skipped; `npm --workspace frontend/admin run test -- --run` -> 8 passed; `npm --workspace frontend/admin run build` passed. Manual browser smoke (symbol/timeframe switching, WS reconnect, responsive breakpoints) was not performed this session — only automated tests and build were run.

## One-click startup dependency self-heal (TASK-030, 2026-07-07)

- The local Paper console startup failure was caused by `frontend/admin/src/router.jsx` importing `@tanstack/react-query` while the workspace `node_modules` did not contain the package, even though `frontend/admin/package.json` and `package-lock.json` already declared it.
- `scripts/start_paper_console.ps1` now checks for key frontend workspace modules (`@tanstack/react-query`, `lightweight-charts`, `react-router-dom`, `vite`, `vitest`) instead of only checking whether `node_modules` exists. If any are missing, one-click startup runs `npm install` before launching FastAPI and Vite.
- This is an Ops/startup-path fix for the local Paper/Testnet console. It does not change Strategy, Validation, Execution, Risk, or Review logic.
- Verification evidence: `npm --workspace frontend/admin run build` passed; `npm --workspace frontend/admin ls @tanstack/react-query` resolved `@tanstack/react-query@5.101.2`; `.\一键启动.bat` started API `http://127.0.0.1:8000` and frontend `http://127.0.0.1:5173`; API `/health` returned ok and frontend `/` returned 200.
- GitHub branch check: local `main`, `origin/main`, and `origin/HEAD` all resolve to `9237b0647174156511ddb138fe76d6fad194d1bb`; the extra remote branches are Dependabot dependency-update branches, not the active platform trunk.

## Trading core scheduler + Binance WS feed bus (TASK-029, 2026-07-07)

- Local Paper operation now has an in-process scheduler at `services/execution/scheduler.py`. FastAPI lifespan starts it when `RUNTIME_SCHEDULER_MODE=inprocess` and autostart is enabled, while Celery remains the production/multi-process path.
- `/api/v1/execution/trading-status` now exposes scheduler mode/running state, last auto-cycle time, next ETA, scheduler error, and live feed status without returning secrets.
- Binance live Kline collection now publishes persisted closed candles through `services/data/live_feed_bus.py`; `/api/v1/market/ohlcv/stream` sends one persisted snapshot and then subscribes to the shared bus instead of polling REST per websocket client.
- Timescale/Postgres OHLCV and market extras writes now use batch `ON CONFLICT DO UPDATE`, with SQLite fallback preserved for tests and local smoke runs.
- The trading console now shows an auto-engine status badge, limit/market order controls with GTC audit metadata, stoploss/takeprofit chart price lines, expanded order columns, and clearer Gatekeeper/LLM rejection reasons.
- Frontend IA has first platform routing: Trading, Risk, Strategy, Validation, Review, Research, and Ops top-level entries. RiskConsole and StrategyLibrary read real existing APIs; other entries are explicit placeholders, not fake data.
- Strategy execution gained optional multi-timeframe confirmation when confirmation bars exist, and Paper notional sizing now uses stop-distance risk budgeting capped by `max_position_fraction` (default 5%) before Gatekeeper.
- Engineering cleanup: CI now runs frontend tests, Python dependency audit, and npm audit reporting; Dependabot is configured; mypy uses explicit package bases; docs/config now reflect Binance-only and single-tenant Bearer decisions.
- Current verification baseline: `py -3 -m pytest -q` -> 146 passed / 1 skipped; Ruff passed; mypy passed; admin Vitest passed; admin build passed; `py -3 scripts/compose_validate.py` skipped because Docker is not on PATH.

## Binance public REST realtime Paper console closure (TASK-028, 2026-07-07)

- Paper/Testnet console now uses Binance public REST as the live market-data path for USD-M Top20 universe, OHLCV, order book, recent trades, and premium-index/funding inputs. If `ccxt` is unavailable, `BinancePublicRestExchange` falls back to standard-library HTTP calls rather than returning fake/static market data.
- `/api/v1/market/ohlcv`, `/snapshot`, `/order-book`, `/trades`, `/universe`, and `/funding-arbitrage-signal` now expose live source evidence such as `binance_public_rest` or `binance_usdm_24h_ticker`, while still writing OHLCV/funding data back into `ohlcv_bars` / `market_extras` for the Validation -> Paper chain.
- Manual Paper/Testnet trading now rejects blank `strategy_id` / `validation_backtest_run_id` at request validation, and Gatekeeper rejection writeback no longer turns blank strategy evidence into a `FailureRecord` model error. Frontend open buttons are disabled until Strategy ID, Backtest ID, and stoploss are present.
- The admin console order book and recent trades panels no longer synthesize local rows from the last price. They render backend order-book/trade payloads, show explicit empty states, and the key trading-console Chinese copy has been repaired to UTF-8 text.
- `scripts/start_paper_console.ps1` and `start-paper-console.bat` provide the local one-click startup path with live public market data enabled, safe Paper/Testnet flags, SQLite schema initialization, FastAPI + Vite startup, and system-browser opening. Browser validation avoids Codex Browser/IAB and uses system Chrome / HTTP smoke only.
- Fresh smoke evidence: local API returned OHLCV/order book/trades from `binance_public_rest`, Top20 from `binance_usdm_24h_ticker`, then a Paper manual long order filled through `paper_manual` and a close request filled with `close_only=true`.
- Current verification baseline: `py -3 -m pytest -q` -> 142 passed / 1 skipped; Ruff passed; mypy passed; admin Vitest passed; admin build passed.

## Open-source RAG assetization closure (TASK-027, 2026-07-06)

- `research_source/open_source_strategy_library` no longer stops at manifest registration. `ResearchSourceAsset` now tracks URL, commit/ref, license, local path, sha256, byte size, status, extraction tags, and summary for local RAG assets.
- `fetch_remote=true` now uses a GitHub allowlist from `seed_sources.json`, writes distilled Markdown assets under `assets/<source_id>/`, and records `asset_manifest.json`; failed remote paths are preserved as `failed_assets` rather than hidden.
- Seed sources now include allowlists, denylist patterns, license policies, and extraction targets, with added RD-Agent, vectorbt, and OpenBB references alongside the earlier Freqtrade/Jesse/Hummingbot/ABU/Lean/vn.py/TradingAgents/Qbot/Superalgos/Nautilus/Qlib/FinRL set.
- Extracted `StrategyIdea` records are now asset-driven: `intake_metadata.asset_refs` is populated from local assets, and unknown-license / metadata-only sources stay `research_note_only` so Strategy Agent cannot materialize them into drafts.
- New research-source APIs expose local assets: `GET /api/v1/research-sources/{source_id}/assets` and `POST /api/v1/research-sources/{source_id}/refresh-assets`.
- First local asset ingestion evidence exists for Freqtrade, Jesse, Hummingbot, ABU, NautilusTrader, Qlib, vectorbt, and OpenBB. This is still a local Markdown/manifest RAG substrate, not a vector DB or full external repository mirror.
- Current verification baseline: `py -3 -m pytest -q` -> 124 passed / 1 skipped; Ruff passed; mypy passed; admin build passed.

## 7x24 Paper decision pipeline automation (TASK-026, 2026-07-06)

- Celery Beat is now configured with real schedules for all-running Paper cycles, market-data heartbeat, risk-profile sweep, daily review generation, notification dispatch, C-level news polling, B-level macro polling, and D-level Twitter watchlist polling. This upgrades the previous worker entrypoint from a manual primitive into an always-on Paper scheduler seam.
- Non-arbitrage Paper order generation now flows through `DecisionPipeline`: persisted MACD/Dow/price-action signals -> `SignalEnsemble` -> `MetaLabel` -> optional `decision_veto_agent.pre_execution_veto_llm` -> `ExecutionOrderRequest`. Funding-threshold arbitrage remains deterministic and bypasses the technical ensemble by design.
- Paper runtime cycles are idempotent per `paper_run_id + symbol + timeframe + latest_bar_time`, and each action can expose a decision trace for frontend/debug/review usage.
- Stoploss/takeprofit generation now prioritizes strategy rules and falls back to ATR/risk-reward distances rather than fixed 2%/3% percentages; Gatekeeper remains the final stoploss/veto/risk hard gate.
- Data Layer now has first C/B/D source seams: `news_items`, macro event storage, RSS/SEC polling, ForexFactory-style macro polling, Twitter watchlist polling, and stale market-data RiskEvents. Missing Twitter credentials produce explicit disabled summaries rather than false success.
- Admin frontend has been split from a single `main.jsx` into API/hooks/pages/components and now includes a Decision Pipeline debug panel plus news, macro, review, and notification visibility. Vitest is now installed for frontend component coverage.
- Current verification baseline: `py -3 -m pytest -q` -> 120 passed / 1 skipped; Ruff passed; mypy passed; admin Vitest passed; admin build passed; compose validation skipped locally because Docker is not on PATH.

## P0 repository hygiene and runtime configuration guardrails (TASK-025, 2026-07-05)

- Runtime database artifacts are no longer allowed in source control. `.dev_ai_quant.db` was removed from Git tracking, and `.gitignore` now covers `.dev_ai_quant.db`, per-process pytest SQLite databases, and SQLite runtime artifacts.
- Docker Compose runtime services now read `.env`; `.env.example` is a template only. CI copies `.env.example` to a temporary `.env` before compose validation, and `scripts/compose_validate.py` rejects runtime compose files that reference `.env.example` as `env_file`.
- Admin API auth remains single-tenant Bearer token only, but the comparison is now constant-time and non-local environments reject the default `dev-admin-token` with `auth_misconfigured`.
- Research Agent local alpha scanning no longer falls back to a workstation-specific desktop path. `scan_local_alpha` requires either `input_payload.alpha_root` or `WORLDQUANT_ALPHA_LOCAL_PATH`.
- User-facing Markdown links are now repository-relative instead of tied to this Windows desktop path. Status docs now describe the repo as `Phase 0 完成 + 第一批 P1 落地`, with the next P1 order fixed as Celery Beat/7x24 scheduling, frontend admin coverage, then B/C/D data sources.

## Autonomous paper runtime over Binance Top20 candidates (TASK-024, 2026-07-04)

- The Execution Layer now has a first autonomous paper-runtime seam: `PaperRuntimeService` plus `/api/v1/execution/paper-runs/{id}/auto-cycle` and `/runtime-status`.
- Paper runtime still respects the existing admission chain. Only validation-admitted `PaperRun` objects can be cycled, and every auto-generated open/close order still flows through `ExecutionGatekeeperService`.
- Default paper monitoring is no longer effectively BTC/ETH-only. `PaperOrchestrationService` now seeds `candidate_symbols` from the in-repo Binance Top20 fallback universe while keeping `BTC/USDT` and `ETH/USDT` pinned first.
- Current runtime behavior is intentionally conservative: opposite signals close existing paper positions before later re-entry, filled paper orders are persisted through `OrderExecution` lifecycle history, and latest open-position state is derived from each symbol's newest `PositionSnapshot`.
- A worker-side entrypoint now exists at `services.execution.tasks.run_paper_runtime_cycle`, but this is still a cycle primitive rather than a proven always-on 7x24 scheduler/daemon.

## Binance testnet-first hardening (TASK-023, 2026-07-04)

- Binance 接入方式已明确收口为官方 API Key / Secret，而不是交易所登录密码。
- `Settings` 新增 `BINANCE_USE_TESTNET` 与 `LIVE_TRADING_ENABLED`；当前默认是 `BINANCE_USE_TESTNET=true`、`LIVE_TRADING_ENABLED=false`，优先测试网 / 模拟盘，不默认放开真实实盘。
- `BinanceUsdtPerpetualGateway` 现在会在可用时对底层 CCXT client 调用 `set_sandbox_mode(True/False)`，让测试网切换成为明确运行时行为，而不是靠人工记忆。
- 运维文档 `docs/ops/environment-and-config.md` 已补成可执行说明，明确要求用户自己在交易所创建测试网或最小权限 API Key，并强调 2FA、IP 白名单、关闭提现权限。

## Tranche 2/3/4 closure slice: validation evidence, live runtime, and online agent boundary (TASK-022, 2026-07-04)

- Validation Layer promotion is now strict across both Paper and Live admission. `HypothesisRecord`, benchmark/control results, OOS windows, and pod-risk evidence are persisted and checked through `ValidationAdmissionService`; legacy backtests without complete evidence no longer promote just because raw backtest eligibility passed.
- Validation report API is hypothesis-aware: `/api/v1/validation/reports/{backtest_run_id}` now resolves the linked hypothesis and returns an accurate `promotion_gate` instead of always showing `missing_hypothesis`.
- Execution Layer now exposes first-class live runtime APIs in the existing `/api/v1/execution/*` cluster: `gateway-capabilities`, `account-snapshots`, `live-runs/{id}/sync-account`, `live-runs/{id}/orders`, `live-runs/{id}/orders/{order_execution_id}/cancel`, `reconciliations`, and `live-runs/{id}/reconcile`.
- The self-owned gateway seam is now materially real rather than placeholder-only: `BinanceUsdtPerpetualGateway` maps account sync, submit, cancel, and reconciliation over a CCXT-style client for `Binance USDT perpetual`, while `NullExchangeGateway` remains the safe no-credentials fallback.
- Agent Layer now has a real structured online boundary instead of only `UnavailableLLMRuntime`: `AnthropicStructuredLLMRuntime` and `ConfiguredStructuredLLMRuntime` call the Anthropic Messages API, enforce JSON-only structured outputs, and allow per-agent provider/model mapping through `AGENT_LLM_PROVIDER_MAP` / `AGENT_LLM_MODEL_MAP`.
- Alembic `0006_validation_memory_and_gateway_runtime.py` now covers the new hypotheses, decision memory, gateway/account snapshot, reconciliation, agent-task metadata, and live/order runtime persistence. The migration was verified against SQLite with a documented SQLite-safe branch for the added live-run foreign key.

## Tranche 1 security + notification dispatch baseline (TASK-020, 2026-07-04)

- `/api/v1/*` now enforces single-tenant Bearer-token auth through `apps/api/auth.py`; `/health` and `/api/v1/health` remain public.
- The admin token is configured by `ADMIN_API_TOKEN`, defaults to `dev-admin-token` for local single-user development, and the Paper admin frontend now sends the same token through `VITE_ADMIN_API_TOKEN` fallback logic.
- `NotificationOutboxItem` is no longer an audit-only seam: it now persists `delivery_channels`, `next_attempt_at`, `last_attempt_at`, and `attempt_history`, and `NotificationDispatcherService` can deliver due items through first-batch `telegram` and `webhook` adapters with persisted retry/backoff state.
- `/api/v1/notifications/outbox/dispatch` can dispatch due notifications or replay one explicit `notification_id`; the same logic is also exposed as Celery task `services.notifications_tasks.dispatch_notification_outbox` on `ops_queue`.
- Frontend build is green again in this workspace after restoring the missing npm workspace dependency install path; CI now includes `npm ci` + `npm run admin:build`.
- `scripts/compose_validate.py` is now the standard compose validation entrypoint. On machines without Docker it exits with a documented `skipped` status locally; CI calls the same script with `--require-docker`.

## Persistent notification outbox (TASK-019, 2026-07-04)

- `NotificationOutboxItem` now persists through `notification_outbox` ORM/migration and `NotificationRepository` instead of being derived only from active `RiskEvent` rows at read time.
- High/critical `RiskEvent` creation automatically enqueues an idempotent pending notification intent with ID `risk:{risk_event_id}`; low/mid events do not auto-enqueue.
- `/api/v1/notifications/outbox` now supports persisted list/filter, manual creation, and delivery-result writeback through `delivery_status`, `delivery_attempts`, `last_error`, and `delivered_at`.
- This is still an Ops / Review / Risk visibility seam only: no Telegram, email, webhook, or credentialed external adapter was added in this tranche.

## Research-side rejection memory writeback (TASK-018, 2026-07-04)

- `FailureRecord` can now attach to either `strategy_id` or `idea_id`, while still rejecting records with neither subject.
- `StrategyIdea.intake_metadata` is now persisted through the shared contract, ORM, repository, and migration, so local alpha intake evidence is structured rather than only embedded in rationale text.
- Research Agent `scan_local_alpha` now writes persisted `subjective_to_drop` / evaluator-rejected alpha ideas into the Review Layer as `alpha_evaluator_reject` failure records.
- `/api/v1/failures` now supports filtering by `strategy_id`, `idea_id`, and `failure_type`, allowing Review/Research workflows to retrieve reusable failure evidence for clustering or manual porting.

## Phase 1 Risk Engine Hardening + WorldQuant Adapter Repair (TASK-017, 2026-07-03)

- Execution Layer now uses a typed `ExecutionRiskState` at order-admission time and persists both `rejection_codes` and `evaluated_risk_state` into every `OrderExecution`.
- `RiskProfile` defaults are now aligned across shared contracts, ORM, migration, and docs: `max_symbol_exposure=0.10`, `max_total_exposure=0.50`, `consecutive_loss_limit=4`, `api_failure_limit=3`, `api_failure_window_minutes=10`.
- `ExecutionGatekeeperService` still enforces stoploss, validation, freshness, veto, and blocking risk events, and now also enforces numeric exposure, leverage, loss, drawdown, consecutive-loss, and API-failure pauses.
- Paper stepping now synthesizes `ExecutionRiskState` from `PaperRun` metrics plus `PositionSnapshot`, while direct execution requests must provide a complete `risk_state` or be rejected.
- Gatekeeper rejections now write structured failure evidence into the existing Review Layer writeback loop through `FailureRecord -> Strategy.failure_reasons + iteration_history`.
- `research_source/worldquant_adapter` is now a real executable research seam instead of a placeholder: `ts_rank`, `ts_zscore`, and `group_neutralize` are implemented; `expression_evaluator.py` executes the supported operator subset over crypto-native inputs and fails loudly on unsupported stock fields/operators.
- Crypto group migration is explicit in v1: `industry -> volatility_regime`, `sector -> funding_regime`, `subindustry -> liquidity_regime`, `market -> market`.
- Local alpha intake now preserves raw expression metadata, windows, operator lists, mapped group aliases, behavior signatures, and explicit unsupported evidence; unsupported expressions are tagged `subjective_to_drop` instead of silently falling through.

## Open-source Strategy Library Intake (TASK-016, 2026-07-03)

- Added `StrategySourceManifest` plus import/extraction request/result contracts for E-level open-source research sources.
- Added `research_source/open_source_strategy_library` with first-batch source manifests for Freqtrade, Jesse, Hummingbot, Lean, vn.py, ABU, Superalgos, Qbot, Vibe-Trading, TradingAgents, TradingAgents-CN, daily_stock_analysis, plus NautilusTrader/OctoBot/QLib/FinRL candidates.
- The module generates local RAG metadata/assets and conservative `StrategyIdea` seeds only; external code is not imported into runtime execution.
- Added `/api/v1/research-sources`, `/api/v1/research-sources/import`, `/api/v1/research-sources/{source_id}`, and `/api/v1/research-sources/{source_id}/extract-ideas`.
- Added Agent tasks: `research_agent.import_open_source_sources`, `research_agent.extract_open_source_strategy_ideas`, and `strategy_agent.materialize_seed_strategy_drafts`.
- Added `PaperSignalGenerator` and `/api/v1/execution/paper-runs/{paper_run_id}/step`; generated paper orders still go through the existing gatekeeper checks for stoploss, validation, freshness, risk events, and veto.
- Verified locally: targeted open-source intake/Paper step tests passed; full `py -3 -m pytest -q` (`52 passed, 1 skipped`); `py -3 -m ruff check .` passed; `py -3 -m mypy` passed; `npm --workspace frontend/admin run build` passed.
- Still out of scope: live execution integration with Freqtrade/Jesse/Hummingbot/Lean/vn.py, full remote repository cloning, vector database indexing, and live grid/market-making.

## Remediation Plan First Pass (TASK-015, 2026-07-03)

- Engineering baseline repaired without adding a new architecture layer: `apps/__init__.py` fixes the package boundary, FastAPI `Depends/Query` Ruff B008 is scoped to router files, and `apps/api/config.py` uses the declared `pydantic-settings` dependency directly.
- Validation Layer now has a carry-lane walk-forward/OOS/stress diagnostic slice in `services/validation/{walk_forward,report,stress_scenarios}.py`; stress results can reject a gate decision and cannot bypass Paper admission.
- Added `/api/v1` endpoints for carry walk-forward, validation reports, system dependency health, exchange capabilities, and notification outbox. Public API prefix remains `/api/v1`.
- `BacktestReport` now carries validation windows, stress results, and lookahead diagnostics; `IngestionJob` carries data quality summary; `ExchangeCapability` and `NotificationOutboxItem` are shared contracts.
- Makefile data/backtest targets now call real script entrypoints or fail explicitly with guidance. Unsupported batch scan/backtest targets no longer pretend success.
- Agent executors are stricter: unknown executor tasks fail, deterministic Decision Veto and Review executor slices exist, and Agent Layer still does not generate orders.
- Documentation synchronized: implementation matrix, technical architecture plan, validation methodology, risk safeguards plan, and ensemble README no longer claim missing modules that now exist.
- Verified locally: `py -3 -m pytest -q` (`45 passed, 1 skipped`), `py -3 -m ruff check .`, `py -3 -m ruff format --check .`, `py -3 -m mypy`, and `npm --workspace frontend/admin run build`.
- Not locally verified: Docker compose config/runtime, because `docker` is not available on PATH. GitHub push remains dependent on network/auth availability.

## Binance Data Layer First Tranche (TASK-014, 2026-07-03)

- Data Layer now has real Binance public-market ingestion seams for first-tranche BTC/USDT use: idempotent `ohlcv_bars` / `market_extras` writes, CCXT-based OHLCV and funding backfill services, and WS payload handlers that persist only closed Kline candles.
- `binance_ohlcv_backfill`, `binance_funding_backfill`, and `binance_live_market_collector` are recognized ingestion job types. `enqueue_binance_ingestion` is registered as a Celery task; backfill jobs write persisted data, while the live collector is a long-lived worker seam and is not a frontend push channel.
- `frontend/admin` now has a Vite dev proxy for `/api -> http://127.0.0.1:8000`, while `VITE_API_BASE_URL` remains an explicit override.
- Timescale init now includes unique indexes for market data idempotency and aligns `risk_events.resolution_status` with the repository.
- Verified locally: `py -3 -m pip install -e ".[dev]"`, targeted Data Layer tests (`11 passed`), changed-file Ruff check, full `py -3 -m pytest -q` (`41 passed`), and `npm --workspace frontend/admin run build`.
- Still not implemented: real order placement/cancel, account balance/position sync, order book persistence, notifications/alerts, news/social ingestion, LLM veto, and frontend WebSocket/SSE push.

## Phase 1a/1b/1d/1e Grounding Update (TASK-012, 2026-07-03)

- `services/data/` has been restored with repository, Binance helpers, application service, and task entrypoints; `.gitignore` now anchors root `/data/` and ignores `.pytest_ai_quant.db` / `*.egg-info/`.
- Dev install now excludes LLM libraries from the default `dev` extra; LLM deps remain optional under `llm`.
- Carry validation no longer uses hardcoded Sharpe/max drawdown/cost constants. It calculates net returns, PnL, Sharpe, max drawdown, profit factor, expectancy, win rate, cost breakdown, and a conservative DSR-style penalty from trade data.
- Negative net expectancy carry samples are rejected; this intentionally changed older tests that expected `conditional` despite failing real net metrics.
- SignalEnsemble / MetaLabel now has a deterministic service and API slice; MACD and Dow swing trend technical signals are implemented. Chan theory remains not implemented.
- WorldQuant alpha semantic evaluator is deferred per latest user instruction; keep only scan/intake seam in scope.
- Verified locally: `py -3 -m pip install -e ".[dev]"`, `py -3 -c "import services.data; import apps.api.main"`, targeted Phase 1 tests (`14 passed`), and full `py -3 -m pytest -q` (`31 passed`).
- Not locally verified: Docker Compose config, because `docker` is unavailable on PATH. Also this directory is not a Git repository, so commits / `git rm --cached` could not be performed here.

## Paper Trading Console Update (TASK-013, 2026-07-03)

- Added `MarketSnapshot`, `OhlcvSeriesResponse`, and `ConsoleOverview` read contracts.
- Added market and console read APIs for the Paper dashboard: `/api/v1/market/snapshot`, `/api/v1/market/ohlcv`, `/api/v1/console/overview`.
- Added Paper status and RiskEvent acknowledgement APIs for first manual controls.
- Rebuilt `frontend/admin` from a static shell into a Paper-first trading console with Binance symbol inputs, Kline chart, carry panel, orders, positions, risk events, and paper/manual controls.
- Added `lightweight-charts` as the frontend chart dependency.
- Verified locally: targeted API tests, full `py -3 -m pytest -q` (`35 passed`), `npm --workspace frontend/admin run build`, and Playwright desktop/mobile smoke. Real backend was not started during browser smoke, so the UI displayed its explicit API failure state as designed.
- Still not implemented: real Binance WebSocket collector, exchange account sync, real order placement/cancel, live trading operations, notifications, and LLM veto execution.

## Identity

- 项目名：AI Quant Research Platform
- 目标：实现研究报告定义的完整量化研究平台，而不是想法体检器或单纯回测脚本
- 主市场：第一阶段以 `BTC/USDT 永续` 为主
- 主语言：Python
- 管理后台：React + Tailwind

## Stable Constraints

- 报告是主架构真源
- 六层架构不可删层
- 风控系统必须是执行层核心，而不是后补
- Review Layer 必须每日回写策略库
- WorldQuant 只作为 E级研究数据与辅助来源

## Current Phase

- Phase 0：平台骨架、统一模型与设计冻结
- 现实进度：已进入“Phase 0 完成 + 第一批 P1 落地”状态，主链已具备可审计的研究闭环骨架

## Active Design Sources

- 第一层真源：研究报告
- 第二层真源：`AGENTS.md`
- 第二层实施母文档：`docs/architecture/platform-master-design.md`
- 开发入口索引：`docs/architecture/design-source-index.md`
- 工程落地细化：`AI_Quant_v2_集成方案开发任务书.pdf` + 对账文档 `docs/architecture/v2-integration-reconciliation.md`（docx 真源优先，冲突以 docx 为准）
- 已完成子设计：`docs/architecture/domain-and-interfaces-design.md`
- 已完成子设计：`docs/architecture/data-and-ingestion-design.md`
- 已完成子设计：`docs/architecture/agent-and-orchestration-design.md`
- 已完成子设计：`docs/architecture/execution-risk-review-design.md`
- 已完成子设计：`docs/architecture/validation-methodology.md`（Deflated Sharpe / walk-forward / 压力测试场景库 / 成本建模）
- 设计附录：
  - `appendix-a-repository-structure.md`
  - `appendix-b-feature-phasing.md`
  - `appendix-c-principles-and-non-goals.md`

## Additional Design Assets

- 上层产品定位：`docs/product/product-spec.md`
- 上层功能总表：`docs/product/feature-catalog.md`
- 开发验收真源：`docs/product/prd.md`
- 模块字段级承接：`docs/product/module-feature-catalog.md`
- 路线图：`docs/roadmap/phase-roadmap.md`
- 配置规范：`docs/ops/environment-and-config.md`
- 准备清单：`docs/ops/delivery-checklist.md`

## Planned Repository Structure

- `apps/api`（已接 strategies CRUD seam + config + celery_app）
- `frontend/admin`
- `shared/models`（统一 Pydantic 数据契约，跨层唯一真源）
- `services/data`
- `services/strategy_library`（已有 Strategy 18 字段 ORM）
- `services/agents`
- `services/validation`
- `services/execution`
- `services/review`
- `research_source/worldquant_adapter`（方法论移植接缝）
- `infra`（timescale/freqtrade/jesse/grafana）
- `migrations`（Alembic，关系表）
- `tests`（contracts + api）

## Phase-0 Scaffolding Status (TASK-005, 2026-06-29)

- 已落地：shared 契约、infra、docker-compose v2（8 服务）、TimescaleDB、Alembic+Strategy ORM、strategies CRUD seam、Makefile、pre-commit、CI、tests。
- 待运行环境验证：`docker compose up`、`alembic upgrade head`、`uv lock`（本地无 docker/make/uv）。
- 下一步：P0-03 `ohlcv_downloader.py`、P0-12 strategies repository（替换内存 seam）。

## Phase-0 收尾架构补强 (TASK-006, 2026-07-02)

用户基于外部量化建议反馈四点，逐一确认后落地为设计文档 + 契约字段（仍不写业务逻辑代码）：

1. 信号融合 + meta-labeling 归属 Strategy Library 子模块（`SignalEnsemble`/`MetaLabel`），不新增独立第7层。见 [domain-and-interfaces-design.md](C:\Users\Windows11\Desktop\量化项目\docs\architecture\domain-and-interfaces-design.md) §3.5a/§3.5b，契约在 `shared/models/signal.py`。
2. P1 优先级明确：资金费率/基差套利（波动率无关底仓策略）先于技术策略框架化落地，用于跑通整条 Validation Layer 流水线。见 [appendix-b-feature-phasing.md](C:\Users\Windows11\Desktop\量化项目\docs\architecture\appendix-b-feature-phasing.md)。
3. 新闻过滤/LLM 一票否决的具体触发规则已写入 [execution-risk-review-design.md §2.2a/§03a](C:\Users\Windows11\Desktop\量化项目\docs\architecture\execution-risk-review-design.md) 与 [agent-and-orchestration-design.md](C:\Users\Windows11\Desktop\量化项目\docs\architecture\agent-and-orchestration-design.md)（新增 Decision Veto Agent）。
4. 验证方法论（walk-forward/Deflated Sharpe/压力测试场景库）与成本建模（手续费/滑点/资金费率净收支）新增子设计文档 [validation-methodology.md](C:\Users\Windows11\Desktop\量化项目\docs\architecture\validation-methodology.md)，`BacktestRun`/`BacktestReport` 契约已补字段。
5. 对应 ADR：见 decisions-log.md ADR-011~014。

## Primary Deliverables For This Phase

1. 全局项目配置
2. 项目记忆体系
3. 统一领域模型
4. 后端主干
5. 初始前端骨架
6. 平台总设计包母文档
7. 领域与接口设计包
8. 数据与接入设计包
9. Agent 与任务编排设计包
10. 执行 / 风控 / 复盘设计包
11. 产品与路线规格包

## Current Executable Status (TASK-011, 2026-07-02)

- API 已统一到 `/api/v1`，列表接口使用 `items + total`，错误返回统一 `error_code/message/detail`
- 已落地真实持久化对象：`OptimizationRun`、`RiskProfile`、`ReviewReport`、`FailureRecord`、`AgentTask`、`LiveRun`、`OrderExecution`、`PositionSnapshot`
- `risk_events` 已从内存假实现切换为 Timescale-owned 持久化事件流
- 执行前 gatekeeper 已拒绝：无止损、validation 未通过、数据不新鲜、`veto=true`、高严重度风险事件
- Review Layer 已支持 `FailureRecord -> Strategy.failure_reasons + iteration_history` 回写
- `research_source/worldquant_adapter` 已具备本地 `alpha` 扫描器，可把研究源转成结构化 `StrategyIdea`
- `frontend/admin` 已不再是占位页，现为 React + Tailwind 管理台壳；本地 build 已通过
- `docker-compose.test.yml`、`docker-compose.paper.yml`、`docker-compose.live.yml` 已入仓，Prometheus/Grafana dashboard 资产已有首版骨架

## Phase-0 开发前完整方案包 (TASK-007, 2026-07-02)

用户要求做第二轮全局查缺补漏，并交付一整套开发前方案文档（技术架构/PRD/模块功能清单/
策略库机制/LLM接入/24小时运行/外部数据源/风控），全部写入 `docs/` 作为正式仓库文档，
分批交付。8 份文档已全部完成：

1. [technical-architecture-plan.md](C:\Users\Windows11\Desktop\量化项目\docs\architecture\technical-architecture-plan.md) — 六层到物理部署的映射、四环境 compose 拓扑规划、API/队列/存储/配置技术规则、§12 已知技术缺口追踪表（当前仓库骨架与目标态之间的具体缺口清单）。
2. [prd.md](C:\Users\Windows11\Desktop\量化项目\docs\product\prd.md) — 产品愿景与反成功标准、两角色、六个人工决策点作为产品硬约束、七模块用户故事+验收标准。
3. [module-feature-catalog.md](C:\Users\Windows11\Desktop\量化项目\docs\product\module-feature-catalog.md) — 逐模块功能点表，精确交叉引用 domain-and-interfaces-design.md 的对象/接口簇字段名。
4. [strategy-library-collection-and-scoring.md](C:\Users\Windows11\Desktop\量化项目\docs\architecture\strategy-library-collection-and-scoring.md) — 六类来源分类、单策略/组合两级评分机制、六类淘汰触发信号，只设计机制不写具体参数。
5. [llm-integration-plan.md](C:\Users\Windows11\Desktop\量化项目\docs\architecture\llm-integration-plan.md) — 逐 Agent LLM 使用边界与禁止清单、四段式 Prompt 结构、成本控制、LangChain/LlamaIndex 仅服务 Research Agent 的 RAG 边界，只做方案不写代码。
6. [24x7-operations-plan.md](C:\Users\Windows11\Desktop\量化项目\docs\architecture\24x7-operations-plan.md) — 进程/连接/数据三层监督模型、心跳与指数退避重连、五级降级状态机、通知告警分级规则。
7. [external-data-source-integration-plan.md](C:\Users\Windows11\Desktop\量化项目\docs\architecture\external-data-source-integration-plan.md) — 五级数据源的具体 API/SDK/拉取频率/存储路径，补全 data-and-ingestion-design.md 有意留白的具体参数。
8. [risk-control-and-safeguards-plan.md](C:\Users\Windows11\Desktop\量化项目\docs\architecture\risk-control-and-safeguards-plan.md) — 裁决此前四处留白：LLM 否决超时选"超时即否决"、Key 权限自检强制拒绝启动规则、`RiskProfile` 具体阈值默认值、熔断触发规则具体化。

对应 ADR：见 decisions-log.md ADR-015~018。

## Design Convergence Blueprint (TASK-008, 2026-07-02)

- 新增 `docs/architecture/design-source-index.md` 作为开发入口索引，固定真源层级、Phase 语义、文档职责边界。
- 明确 `Phase 0` = 平台骨架 + 统一模型 + 设计冻结；`appendix-b-feature-phasing.md` 中的 `P0/P1/P2` 仅是实现 tranche 标签。
- README / `report-alignment.md` / 产品上层文档已同步说明：`product-spec.md` + `feature-catalog.md` 负责上层定位，`prd.md` + `module-feature-catalog.md` 负责开发验收。
- `策略库/笔记.docx` 被正式定义为研究素材池，只能通过 `StrategyIdea -> StrategyDraft -> StrategyContract` 流程进入主链路。
- 代码层补齐：`RiskProfile` 契约、工作流生命周期对象契约、六大接口簇 API skeleton、全量 `Settings` 环境变量入口、`services/data` 包骨架。
## First Persisted Vertical Slice (TASK-009, 2026-07-02)

- The repository now has persisted SQLAlchemy-backed repositories for `StrategyIdea`, `StrategyDraft`, `Strategy`, `StrategyVersion`, `BacktestRun`, `IngestionJob`, and `PaperRun`.
- `apps/api/routers/{strategies,backtests,ingestion,runs}.py` now use real repositories via `get_db_session` instead of in-memory dictionaries.
- Binance A-level helpers now exist in `services/data/binance.py` for `Top20` universe selection, OHLCV normalization, and funding-rate normalization.
- `services/data/service.py` defines the current fallback Binance `Top20` universe list for ingestion jobs until live exchange ranking is wired in.
- `services/validation/carry.py` contains the first carry backtest service with settlement-window handling, spot/perp/funding cost reconciliation, and `conditional` gate behavior when deflated Sharpe is absent.
- `services/execution/paper.py` makes `BTC/USDT` and `ETH/USDT` the default first simulated symbols for paper preparation.
- Celery now has first real task entrypoints for ingestion, backtests, and paper runs.
- Alembic `0001` now manages the relational tables for this slice instead of only the original `strategies` table.
- Current verified path: `StrategyIdea -> StrategyDraft -> Strategy -> StrategyVersion -> BacktestRun -> GateDecision -> PaperRun`.

## Persisted Carry Application Flow (TASK-010, 2026-07-02)

- `services/data/repository.py` now provides the first real timeseries repository for `ohlcv_bars` and `market_extras`, including store/list helpers plus gap and freshness checks for Phase-1 carry validation.
- `shared.models.CarryBacktestRequest` is now the application-layer submission contract for the persisted carry lane.
- `services/validation/application.py` now defines `CarryBacktestApplicationService`, which loads persisted spot/perp/funding data, applies settlement-window data-quality checks, runs the carry backtest, and persists the resulting `BacktestRun`.
- `apps/api/routers/backtests.py` now exposes `/backtests/carry` as the first API path that executes a backtest from persisted market data instead of accepting only a pre-built `BacktestRun` payload.
- `services.validation.tasks.enqueue_carry_backtest` is wired as the queue-side entrypoint for the same application flow, but local import smoke for Celery remains pending until the environment installs `celery`.
