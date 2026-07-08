# Task History

### [TASK-031] Real-time trading console + multi-screen split
- **Date**: 2026-07-07
- **Type**: fix + refactor + frontend
- **Summary**: Addressed the user complaint that the Paper trading console was not actually real-time and crammed everything into one screen. Turned on the three Binance live-data feature flags that were defaulting to `False` (universe/market/WS), added an autouse pytest fixture to force them back off during tests, stopped `useConsoleData.js` from forcing a full chart rebuild on every 8s poll tick while the WS stream is live (klines now flow through the WS `kline` event's incremental `update()` only), added exponential-backoff WS reconnect on `onclose`/`onerror`, reordered the trading page's CSS grid so the order ticket is reachable within one screen below the 1280px breakpoint, and migrated non-core-trading panels (risk events, news/macro/notifications, review reports) out of `PaperConsole.jsx` into the already-scaffolded `/risk`, `/ops`, `/review` routes (`RiskConsole.jsx`, `OpsConsole.jsx`, `ReviewCenter.jsx`), replacing their placeholder content with real `useQuery`-backed panels reusing `FeedPanel` (newly exported from `OpsPanels.jsx`) and `RiskEventFeed`.
- **Files changed**: `apps/api/config.py`, `tests/conftest.py`, `frontend/admin/src/hooks/useConsoleData.js`, `frontend/admin/src/pages/{PaperConsole,RiskConsole,OpsConsole,ReviewCenter}.jsx`, `frontend/admin/src/components/OpsPanels.jsx`, `frontend/admin/src/styles.css`, `.github/agent/memory/project-memory.md`, `.github/agent/memory/task-history.md`.
- **Layer mapping**: Data Layer owns the live-data config flags governing Binance REST/WS sourcing; Execution Layer's operator-facing frontend owns the trading console layout, real-time chart update logic, and the risk/ops/review console pages. No Strategy, Validation, or Execution decision/order logic changed.
- **Research loop served**: Keeps the Paper-mode operator console (`Data -> Validation/Paper -> Execution Gatekeeper -> Review`) genuinely inspectable in real time, and separates the trading-decision surface from ops/risk/review surfaces so each can be reviewed on its own screen without diluting the trading view.
- **Verification**: `py -3 -m pytest tests/ -q` -> 151 passed, 1 skipped; `npm --workspace frontend/admin run test -- --run` -> 3 files / 8 tests passed; `npm --workspace frontend/admin run build` -> succeeded (102 modules transformed, no errors). Manual browser smoke of symbol/timeframe switching and WS reconnect was not performed this session.
- **Notes**: Deliberate deviation from the original plan text — `RuntimeControlPanel`/`DecisionDebugPanel` were kept on `/trading` (inside an always-visible `execution-grid`, not an accordion) instead of moving to `/ops`, because both are scoped to the currently-selected symbol/timeframe rather than global ops state. `OpsReviewPanel` and the now-unused `newsItems`/`macroEvents`/`reviews`/`notifications` fetch/state fields in `useConsoleData.js` were deleted after confirming zero remaining callers via Grep.

### [TASK-030] Fix one-click startup dependency self-heal
- **Date**: 2026-07-07
- **Type**: fix + ops
- **Summary**: Repaired the local one-click Paper console startup path after Vite failed to resolve `@tanstack/react-query` from `frontend/admin/src/router.jsx`. The package was already declared in the frontend workspace and lockfile, but the existing startup script skipped `npm install` whenever root `node_modules` existed, leaving newly added dependencies absent.
- **Files changed**: `scripts/start_paper_console.ps1`, `.github/agent/memory/project-memory.md`, `.github/agent/memory/task-history.md`
- **Layer mapping**: Ops/startup tooling only. No Strategy, Validation, Execution, Risk, Review, or trading-decision behavior changed.
- **Research loop served**: Keeps the local Paper/Testnet operator console launchable so the existing `Data -> Validation/Paper -> Execution Gatekeeper -> Review` workflow can be inspected without bypassing platform gates.
- **Verification**: `npm install` installed the missing frontend packages; `npm --workspace frontend/admin run build` passed; `npm --workspace frontend/admin ls @tanstack/react-query` resolved `@tanstack/react-query@5.101.2`; `.\一键启动.bat` successfully started FastAPI and Vite; API `/health` returned ok and frontend `/` returned HTTP 200.
- **Notes**: `npm install` still reports the known 5 frontend audit vulnerabilities; no `npm audit fix --force` was run. Local `main`, `origin/main`, and `origin/HEAD` all point to `9237b0647174156511ddb138fe76d6fad194d1bb`; the additional remote branches are Dependabot dependency-update branches.

### [TASK-029] Trading core scheduler, live feed bus, and platform console refactor
- **Date**: 2026-07-07
- **Type**: feat + refactor + frontend + ops + docs
- **Summary**: Implemented the trading-core refactor plan. Added a local in-process `RuntimeScheduler` that calls the existing Celery task bodies for Paper cycles, market heartbeat, risk sweep, notifications, and daily review; extended `trading-status` with scheduler/feed observability; connected Binance WS closed Kline collection to a shared `LiveFeedBus`; rewired `/market/ohlcv/stream` away from per-client REST polling; added Postgres batch upsert paths for OHLCV/extras; and updated the one-click Paper console script to run 60s in-process cycles with optional WS feed.
- **Files changed**: `services/execution/scheduler.py`, `services/data/live_feed_bus.py`, `services/data/{binance,repository,__init__}.py`, `apps/api/{main,config}.py`, `apps/api/routers/{market,runs}.py`, `shared/models/{execution_runtime,workflow,enums}.py`, `frontend/admin/**`, CI/docs/config files, and targeted tests.
- **Layer mapping**: Data Layer owns Binance WS feed normalization, fan-out, and Timescale upserts; Execution Layer owns in-process scheduling and Paper notional sizing; Strategy Layer owns optional multi-timeframe confirmation; Frontend Admin owns operator visibility; Ops/CI owns dependency scanning and scheduler validation.
- **Research loop served**: The local runtime now repeatedly drives `Validated PaperRun -> DecisionPipeline -> Gatekeeper -> OrderExecution/PositionSnapshot -> Review/notification`, while live Klines flow through `Binance WS -> DataRepository -> LiveFeedBus -> frontend websocket` without bypassing Validation/Risk.
- **Verification**: `py -3 -m pytest -q` -> 146 passed, 1 skipped; `py -3 -m ruff check .` passed; `py -3 -m mypy` passed; `npm --workspace frontend/admin run test` passed; `npm --workspace frontend/admin run build` passed; `py -3 scripts/compose_validate.py` skipped because Docker is not on PATH.
- **Notes**: `npm install` still reports 5 existing frontend audit vulnerabilities; no `npm audit fix --force` was run. CI now records npm audit and fails Python dependency audit. OKX/Bybit remain future enum placeholders only; auth remains single-tenant Bearer Token.

### [TASK-028] Complete Binance realtime Paper console data and manual open/close smoke
- **Date**: 2026-07-07
- **Type**: feat + fix + frontend + ops + verification
- **Summary**: Repaired the Paper console so it no longer appears as a static shell. Added Binance public REST live reads for USD-M Top20, OHLCV, order book, recent trades, and premiumIndex/funding with a standard-library fallback when `ccxt` is not installed; wired market APIs to refresh and persist live OHLCV/funding data; added explicit order-book/trade contracts; fixed blank manual-order validation evidence causing `FailureRecord` errors; disabled frontend open buttons until Strategy ID, Backtest ID, and stoploss are present; replaced synthetic frontend order book/trades with backend live payloads; repaired key Chinese mojibake; and hardened the one-click startup script's port handling.
- **Files changed**: `services/data/{binance,market}.py`, `apps/api/routers/market.py`, `shared/models/{market,workflow,__init__}.py`, `services/execution/gatekeeper.py`, `frontend/admin/src/{api,hooks,pages,components,utils,styles}.js*`, `scripts/start_paper_console.ps1`, `start-paper-console.bat`, and API/service/frontend tests.
- **Layer mapping**: Data Layer owns Binance public REST live reads and persistence into `ohlcv_bars` / `market_extras`; Execution Layer owns manual Paper/Testnet order admission and close-only handling; Review Layer remains the rejection-memory sink but no longer receives invalid blank-subject failures; Frontend is an operator surface only and never connects directly to Binance.
- **Research loop served**: Live public market data now feeds `Data -> Validation evidence -> Manual/Auto Paper order -> Gatekeeper -> OrderExecution/PositionSnapshot -> Review`, preserving the required Strategy/Validation/Risk chain while making the trading console operationally inspectable.
- **Verification**: `py -3 -m pytest -q` -> 142 passed, 1 skipped; `py -3 -m ruff check .` passed; `py -3 -m mypy` passed; `npm --workspace frontend/admin run test -- --run` passed; `npm --workspace frontend/admin run build` passed.
- **Runtime smoke**: `scripts/start_paper_console.ps1` started FastAPI and Vite and opened `http://127.0.0.1:5173` via the system browser. HTTP smoke confirmed OHLCV/order book/trades source `binance_public_rest`, Top20 source `binance_usdm_24h_ticker`, a Paper manual order `filled` through `paper_manual`, and Paper close `filled` with `close_only=true`.
- **Notes**: Browser/IAB was not used because this Windows Codex Desktop is configured Chrome-only with bundled Browser disabled. Mainnet real trading remains out of scope; this is Paper/Testnet only.

### [TASK-026] Implement 7x24 Paper decision pipeline automation
- **Date**: 2026-07-06
- **Type**: feat + fix + frontend + ops
- **Summary**: Implemented the approved full A-F plan for the Binance-only / Paper-only 7x24 automation loop. Added Celery Beat schedules for Paper cycles, market heartbeat, risk sweep, daily review, notifications, news, macro, and Twitter watchlist polling; added `DecisionPipeline` to connect technical signals, price action, SignalEnsemble, MetaLabel, and Decision Veto Agent into real Paper order generation; replaced fixed stoploss/takeprofit percentages with strategy-rule/ATR risk prices; added cycle idempotency keys and decision traces; added news/macro/social data seams and stale-data RiskEvents; split the admin frontend into API/hooks/pages/components and added a Decision Pipeline debug panel with Vitest coverage.
- **Files changed**: `apps/api/{celery_app.py,config.py,routers/{market,runs,system}.py}`, `services/{data,execution,review,strategy_library/technical}/**`, `shared/models/{enums.py,workflow.py}`, `infra/timescale/init.sql`, `frontend/admin/**`, `tests/{api,services}/**`, and memory files.
- **Layer mapping**: Data Layer owns heartbeat/news/macro/social capture; Strategy Layer owns technical signals and ensemble/meta-label decisions; Agent Layer owns LLM classification/veto tasks; Execution Layer owns `DecisionPipeline`, Paper idempotent cycles, ATR stop plans, and Gatekeeper admission; Review/Ops own daily reports, notifications, and decision visibility. No seventh layer was introduced.
- **Research loop served**: The automatic Paper path now follows `Validated PaperRun -> DecisionPipeline -> ExecutionOrderRequest -> Gatekeeper -> OrderExecution/PositionSnapshot -> Review/Failure/decision trace`, so non-arbitrage orders are traceable to technical signals, ensemble confidence, meta-label sizing, LLM veto, and risk checks.
- **Verification**: `py -3 -m pytest -q` -> 120 passed, 1 skipped; `py -3 -m ruff check .` passed; `py -3 -m mypy` passed; `npm --workspace frontend/admin run test` passed; `npm --workspace frontend/admin run build` passed; `py -3 scripts/compose_validate.py` -> skipped because Docker is not on PATH.
- **Notes**: `npm install --workspace frontend/admin` reported 5 audit vulnerabilities in the frontend dependency tree; no forced audit fix was run because it may introduce breaking upgrades. Real RSS/Twitter/LLM calls still depend on operator-provided network credentials and live environment availability.

### [TASK-025] P0 repository hygiene and runtime configuration hardening
- **Date**: 2026-07-05
- **Type**: fix + ops + docs + tests
- **Summary**: Implemented the P0 remediation plan. Removed the tracked `.dev_ai_quant.db` runtime database from Git tracking, expanded `.gitignore` for runtime DB artifacts, changed compose runtime env files from `.env.example` to `.env`, made CI prepare a temporary `.env` for compose validation, added compose/repository/Markdown portability guard tests, hardened admin Bearer auth with constant-time comparison plus non-local default-token rejection, removed the Research Agent's workstation-specific alpha fallback path, and synchronized status docs to `Phase 0 完成 + 第一批 P1 落地`.
- **Files changed**: `.gitignore`, `.github/workflows/ci.yml`, `docker-compose.yml`, `scripts/compose_validate.py`, `apps/api/auth.py`, `services/agents/service.py`, repository hygiene/portability/auth/agent tests, README/AGENTS/docs status files, and `.github/agent/memory/{project-memory.md,decisions-log.md,task-history.md}`.
- **Layer mapping**: This is an Ops/API/Agent-boundary hardening task. It does not add a new layer, alter strategy logic, or bypass the required `Strategy -> Validation -> Execution -> Review` chain.
- **Research loop served**: Keeps the research platform portable and safer to operate before adding 7x24 scheduling by ensuring runtime state and templates do not leak into source control, auth fails closed outside local development, and local research intake paths are explicit.
- **Verification**: `py -3 -m pytest -q` -> 116 passed, 1 skipped; `py -3 -m ruff check .` passed; `py -3 -m mypy` passed; `npm --workspace frontend/admin run build` passed; `py -3 scripts/compose_validate.py` -> `[skipped] docker not found on PATH; compose runtime validation skipped`.
- **Notes**: No Git history rewrite was performed. `.dev_ai_quant.db` had previously been inspected as an empty schema-only SQLite database, and this task only removes it from future tracked content.

### [TASK-024] Add autonomous paper-runtime cycle over the admitted Top20 candidate universe
- **Date**: 2026-07-04
- **Type**: feat + execution
- **Summary**: Added the first autonomous paper-runtime slice inside the existing Execution Layer. Paper runs can now execute `/api/v1/execution/paper-runs/{id}/auto-cycle` to scan candidate symbols, open paper positions on fresh admitted signals, close positions on opposite signals, persist filled order lifecycle updates, and expose `/runtime-status` for the current open-position view. The default paper candidate universe is now Binance Top20 with BTC/ETH still pinned first, and a Celery task `services.execution.tasks.run_paper_runtime_cycle` provides the worker-side entrypoint for repeated scheduling.
- **Files changed**: `apps/api/routers/runs.py`, `services/execution/{__init__.py,gatekeeper.py,paper.py,paper_runtime.py,tasks.py}`, `services/strategy_library/repository.py`, `shared/models/{__init__.py,workflow.py}`, `tests/api/test_paper_runtime_api.py`, `.github/agent/memory/{project-memory.md,decisions-log.md,task-history.md}`
- **Layer mapping**: This stays inside the Execution Layer and shared contracts only. Validation admission is still enforced upstream through the existing `gate_decision_ref` + gatekeeper path, and no Agent or UI path bypasses `Validation -> Execution`.
- **Research loop served**: `BacktestRun admission evidence -> PaperRun -> auto cycle -> gatekeeper -> filled paper order -> position snapshot/runtime status` is now explicit, test-covered, and reusable for later Celery scheduling or testnet expansion.
- **Verification**: `C:\Users\Windows11\AppData\Local\Programs\Python\Python312\python.exe -m pytest -q` -> 108 passed, 1 skipped; `...python.exe -m ruff check .` passed; `...python.exe -m mypy` passed.
- **Notes**: This is a repeatable autonomous paper cycle plus worker entrypoint, not a proven 7x24 production daemon yet. Docker runtime smoke remains skipped locally because `docker` is not on PATH.

### [TASK-023] Harden Binance access toward testnet-first API credentials
- **Date**: 2026-07-04
- **Type**: security + ops
- **Summary**: Refused unsafe use of exchange login credentials and tightened the repo toward the intended integration path: exchange-owned API keys on testnet or paper first. Added `BINANCE_USE_TESTNET` and `LIVE_TRADING_ENABLED` settings, made `BinanceUsdtPerpetualGateway` propagate sandbox mode to the underlying CCXT client when available, and rewrote the environment/config ops guide so operators configure testnet keys instead of reusing account passwords.
- **Files changed**: `apps/api/config.py`, `.env.example`, `services/execution/gateway.py`, `tests/services/test_binance_gateway.py`, `docs/ops/environment-and-config.md`, `.github/agent/memory/{project-memory.md,task-history.md}`
- **Layer mapping**: This is an Ops / Execution boundary hardening change. It does not add a new layer or expand strategy logic; it constrains how exchange connectivity is enabled.
- **Research loop served**: Keeps `Validation -> Paper -> Live` progression safe by default, ensuring exchange connectivity starts from testnet/sandbox instead of direct real-account login credentials.
- **Verification**: `C:\Users\Windows11\AppData\Local\Programs\Python\Python312\python.exe -m pytest -q tests/services/test_binance_gateway.py tests/services/test_exchange_gateway.py tests/api/test_execution_runtime_api.py` -> 5 passed; `...python.exe -m ruff check apps/api/config.py services/execution/gateway.py tests/services/test_binance_gateway.py` passed; `...python.exe -m mypy apps/api/config.py services/execution/gateway.py` passed.
- **Notes**: In this restricted follow-up environment, `py -3` was unavailable, so verification used the explicit Python 3.12 interpreter path.

### [TASK-022] Complete strict promotion evidence, live runtime APIs, and online agent/gateway boundaries
- **Date**: 2026-07-04
- **Type**: feat + verification
- **Summary**: Completed the next remaining-platform closure slice after the Tranche 1 baseline. Tightened Paper/Live promotion so raw backtest pass no longer bypasses missing hypothesis/benchmark/OOS/pod-risk evidence; made validation reports hypothesis-aware; added live runtime APIs for gateway capabilities, account snapshot sync/query, live order submit/cancel, and reconciliation query/trigger; added the first real Binance USDT perpetual gateway implementation over a CCXT-style client boundary; added a real Anthropic structured-output runtime plus per-agent provider/model mapping; and added Alembic `0006` for hypotheses, decision memory, gateway snapshots, reconciliation, and runtime metadata persistence.
- **Files changed**: `apps/api/routers/{agents,backtests,runs}.py`, `apps/api/config.py`, `services/agents/{__init__,llm_runtime,service}.py`, `services/execution/{__init__,gateway}.py`, `services/validation/report.py`, `migrations/versions/0006_validation_memory_and_gateway_runtime.py`, `.env.example`, new API/service tests, and memory files.
- **Layer mapping**: Validation evidence enforcement stays in the Validation Layer; gateway/account/reconciliation runtime stays in the Execution Layer; structured online LLM calls stay in the Agent Layer; decision memory remains inside the existing Review deployment boundary. No seventh layer was introduced and no route bypasses `Validation -> Execution`.
- **Research loop served**: `Hypothesis -> BacktestRun -> promotion_gate -> Paper/Live admission -> gateway lifecycle/reconciliation -> decision memory/review evidence` is now explicit and auditable, while `News/Twitter/Telegram/Decision Veto` tasks can use a real online structured LLM boundary without ever generating orders directly.
- **Verification**: `py -3 -m pytest -q` -> 106 passed, 1 skipped; `py -3 -m ruff check .` passed; `py -3 -m mypy` passed; `Remove-Item .verify_ai_quant.db; $env:POSTGRES_URL='sqlite:///./.verify_ai_quant.db'; py -3 -m alembic upgrade head` passed through `0006`; `npm --workspace frontend/admin run build` passed; `py -3 scripts/compose_validate.py` -> `[skipped] docker not found on PATH; compose runtime validation skipped`.
- **Notes**: Docker runtime verification is still host-dependent because `docker` is not on PATH locally. Binance/Anthropic online runtime paths are implemented and test-covered at the boundary level, but real credentialed end-to-end exchange/LLM execution still depends on operator-provided secrets and a live environment.

### [TASK-021] Sync Tranche 1 status docs and re-verify baseline
- **Date**: 2026-07-04
- **Type**: docs + verification
- **Summary**: Synchronized the remaining stale status documents after the Tranche 1 auth/notification/ops implementation landed. Updated the implementation matrix, technical architecture plan, and delivery checklist so they now reflect the real single-tenant auth baseline, Telegram/Webhook notification dispatch path, restored `frontend/admin` build, and scripted `compose-validate` workflow with the documented local Docker limitation.
- **Files changed**: `docs/architecture/{implementation-status-matrix.md,technical-architecture-plan.md}`, `docs/ops/delivery-checklist.md`, `.github/agent/memory/task-history.md`
- **Layer mapping**: This change updates architecture/ops/status documentation only. It does not introduce new runtime modules or alter the six-layer boundary.
- **Research loop served**: Keeps the operator and developer view aligned with the real admission/ops baseline, reducing the risk of planning future Validation / Execution / Review work against stale assumptions.
- **Verification**: `py -3 -m pytest -q` -> 89 passed, 1 skipped; `py -3 -m ruff check .` passed; `py -3 -m mypy` passed; `npm --workspace frontend/admin run build` passed; `py -3 scripts/compose_validate.py` -> `[skipped] docker not found on PATH; compose runtime validation skipped`.
- **Notes**: No new ADR was needed. Local Docker runtime verification remains pending on a host with Docker available.

### [TASK-020] Implement Tranche 1 security + notification dispatch baseline
- **Date**: 2026-07-04
- **Type**: feat + ops + auth
- **Summary**: Implemented the first tranche from the remaining-platform roadmap. Added single-tenant Bearer auth for `/api/v1/*` while keeping health endpoints public; upgraded notification outbox from persisted intent into a real dispatch loop with Telegram/Webhook adapters, retry/backoff state, attempt history, API replay endpoint, and Celery task; restored the frontend admin build by reinstalling workspace dependencies and wiring the admin token into requests; and added a script-backed compose validation path for local/CI use.
- **Files changed**: `apps/api/{auth.py,main.py,config.py,celery_app.py,routers/notifications.py}`, `services/{notifications.py,notifications_tasks.py,strategy_library/{models.py,repository.py}}`, `shared/models/workflow.py`, `frontend/admin/src/main.jsx`, `.env.example`, `scripts/{__init__.py,compose_validate.py}`, `.github/workflows/ci.yml`, `Makefile`, `migrations/versions/0005_notification_dispatch_runtime_fields.py`, notification/auth/compose tests, status docs, and memory files.
- **Layer mapping**: Auth belongs to the API boundary; notification dispatch belongs to Ops / Review / Risk visibility and response inside the existing six-layer architecture; compose validation and CI wiring are operational guardrails rather than new product modules.
- **Research loop served**: `RiskEvent -> NotificationOutboxItem -> dispatcher -> adapter result/audit history` is now a real operational closure, while API auth protects the operator surface that controls validation, paper runs, and risk actions.
- **Verification**: `py -3 -m pytest -q` -> 89 passed, 1 skipped; `py -3 -m ruff check .` passed; `py -3 -m ruff format --check <changed-files>` passed; `py -3 -m mypy` passed; `npm --workspace frontend/admin run build` passed; `py -3 scripts/compose_validate.py` -> `[skipped] docker not found on PATH; compose runtime validation skipped`; `$env:POSTGRES_URL='sqlite:///./.verify_ai_quant.db'; py -3 -m alembic upgrade head` passed from `0001` to `0005`.
- **Notes**: This completes the planned Tranche 1 baseline only. Full DSR / hypothesis registry / decision memory service / live exchange gateway / real LLM agents remain future tranches.

### [TASK-019] Persist notification outbox intents and delivery status
- **Date**: 2026-07-04
- **Type**: feat
- **Summary**: Upgraded the notification outbox from a read-time derivation over active risk events into a persisted Ops/Review/Risk visibility channel. Added `notification_outbox` ORM/migration, `NotificationRepository`, delivery-status fields, persisted outbox APIs for list/filter/manual create/delivery update, and automatic idempotent notification enqueueing for high/critical `RiskEvent` creation.
- **Files changed**: `shared/models/{workflow,__init__}.py`, `services/{notifications.py,strategy_library/**}`, `apps/api/routers/{risk,notifications}.py`, `migrations/versions/0004_persist_notification_outbox.py`, `tests/api/test_remediation_plan.py`, status docs, and memory files.
- **Layer mapping**: Notification outbox belongs to Ops / Review / Risk visibility inside the existing six-layer architecture. It records notification intent and adapter results only; no real Telegram/email/webhook adapter or new Agent subsystem was added.
- **Research loop served**: `RiskEvent -> NotificationOutboxItem -> delivery_status audit` now keeps operational evidence available after a risk event is resolved, so Review/Ops can reuse the same durable audit trail.
- **Verification**: Red-green targeted notification tests passed; targeted remediation/shared/repository tests -> 18 passed; full `py -3 -m pytest -q` -> 80 passed, 1 skipped; `py -3 -m ruff check .` passed; `py -3 -m mypy` passed; `$env:POSTGRES_URL='sqlite:///./.verify_ai_quant.db'; py -3 -m alembic upgrade head` passed and the temporary SQLite DB was removed.
- **Notes**: Real outbound adapters and credentials remain future work. High/critical risk events auto-create notification intents; low/mid events do not.

### [TASK-018] Route alpha evaluator rejections into Review failure memory
- **Date**: 2026-07-04
- **Type**: feat
- **Summary**: Completed the remaining decision-memory slice after TASK-017. Persisted `StrategyIdea.intake_metadata`, allowed `FailureRecord` to attach to `idea_id` as well as `strategy_id`, wired Research Agent `scan_local_alpha` so persisted `subjective_to_drop` alpha ideas create `alpha_evaluator_reject` failure records, and added `/api/v1/failures` filters for `strategy_id`, `idea_id`, and `failure_type`.
- **Files changed**: `shared/models/{strategy,workflow}.py`, `services/{agents,review,strategy_library}/**`, `apps/api/routers/{agents,review}.py`, `research_source/worldquant_adapter/local_alpha_scanner.py`, `migrations/versions/0003_harden_risk_engine_and_alpha_audit.py`, targeted tests, and memory files.
- **Layer mapping**: Local alpha scanning remains Data Layer E-level research intake feeding Strategy Layer ideas; `FailureRecord` writeback and `/failures` retrieval belong to the Review Layer; Agent orchestration only coordinates structured objects.
- **Research loop served**: `Alpha expression -> AlphaPlan -> Evaluator -> StrategyIdea.intake_metadata -> FailureRecord` is now reusable by Review/Research without re-parsing rationale text.
- **Verification**: Targeted tests (`tests/contracts/test_shared_models.py`, `tests/repositories/test_strategy_repository.py`, `tests/api/test_risk_review_agents.py`, `tests/research_source/test_worldquant_adapter.py`) -> 24 passed; full `py -3 -m pytest -q` -> 79 passed, 1 skipped; `py -3 -m ruff check .` passed; `py -3 -m mypy` passed; `$env:POSTGRES_URL='sqlite:///./.verify_ai_quant.db'; py -3 -m alembic upgrade head` passed.
- **Notes**: Temporary SQLite verification database was removed after migration smoke. No new autonomous memory subsystem was added.

### [TASK-027] Open-source RAG assetization and intake reconciliation
- **Date**: 2026-07-06
- **Type**: feat + docs
- **Summary**: Upgraded open-source strategy intake from manifest-only registration to traceable local RAG assets. Added `ResearchSourceAsset`, GitHub allowlist fetching, distilled Markdown assets, per-source `asset_manifest.json`, source allowlists/denylists/license policies/extraction targets, asset-driven `StrategyIdea` metadata, research-source asset APIs, and Agent output fields for imported/failed assets. Added RD-Agent, vectorbt, and OpenBB to the seed set and reconciled status docs.
- **Files changed**: `shared/models/research_source.py`, `research_source/open_source_strategy_library/**`, `apps/api/routers/research_sources.py`, `services/agents/service.py`, tests, docs, and memory files.
- **Layer mapping**: Open-source asset fetching belongs to Data Layer E-level research intake; asset-driven `StrategyIdea` records feed the Strategy Layer; Paper/Execution remain gated by existing Validation and Gatekeeper services.
- **Research loop served**: `StrategySourceManifest -> ResearchSourceAsset -> StrategyIdea -> StrategyDraft -> Strategy -> BacktestRun -> PaperRun -> Gatekeeper`, without importing external runtime code.
- **Verification**: `py -3 -m pytest -q` -> 124 passed, 1 skipped; `py -3 -m ruff check .` passed; `py -3 -m mypy` passed; `npm --workspace frontend/admin run build` passed.
- **Notes**: Real fetch evidence exists for Freqtrade/Jesse/Hummingbot/ABU/NautilusTrader/Qlib/vectorbt/OpenBB. Remaining gaps: vector DB/LlamaIndex indexing, deep LLM research reports, full repo mirrors, Docker runtime smoke, and credentialed 24h external API validation.

### [TASK-017] Harden Risk Engine admission and repair WorldQuant executable intake
- **Date**: 2026-07-03
- **Type**: feat + fix
- **Summary**: Completed the approved Phase 1 slice that hardens order admission and removes WorldQuant placeholders. Added `ExecutionRiskState`, aligned `RiskProfile` defaults and persistence, extended `ExecutionGatekeeperService` with numeric risk checks and structured rejection audit fields, and ensured Paper stepping synthesizes the same runtime risk snapshot used by direct execution. Replaced the WorldQuant placeholder generator with a real evaluator-backed path, implemented `ts_rank` / `ts_zscore` / `group_neutralize`, added explicit crypto group alias migration, and upgraded local alpha intake to preserve behavior signatures and unsupported evidence.
- **Files changed**: `shared/models/{risk,workflow,alpha,__init__}.py`, `services/{execution,strategy_library}/**`, `apps/api/routers/runs.py`, `research_source/worldquant_adapter/**`, `migrations/versions/0003_harden_risk_engine_and_alpha_audit.py`, targeted docs, tests, and memory files.
- **Layer mapping**: `ExecutionRiskState` + gatekeeper numeric checks belong to the Execution Layer / Risk Engine; `FailureRecord` writeback belongs to the Review Layer; WorldQuant parser/evaluator/scanner remain Data Layer E-level research intake feeding Strategy Layer seeds.
- **Research loop served**: `RiskProfile -> ExecutionGatekeeper -> OrderExecution/FailureRecord` is now auditable end-to-end, and `Alpha expression -> AlphaPlan -> Evaluator -> CryptoFactorGenerator -> StrategyIdea` is now explicit about what is executable versus research-only.
- **Verification**: `py -3 -m pytest -q` -> 76 passed, 1 skipped; `py -3 -m ruff check .` passed; `py -3 -m mypy` passed; `$env:POSTGRES_URL='sqlite:///./.verify_ai_quant.db'; py -3 -m alembic upgrade head` passed.
- **Notes**: To run repository verification locally in this environment, I installed the missing declared/dev dependencies `pydantic-settings`, `ruff`, `celery`, `mypy`, and `pytest-asyncio` into the active Python interpreter because they were absent at session start.

### [TASK-016] Open-source strategy library intake and Paper order stepping
- **Date**: 2026-07-03
- **Type**: feat
- **Summary**: Added the E-level open-source strategy intake path. Registered first-batch GitHub strategy/research/LLM workflow sources as `StrategySourceManifest`, added local RAG asset indexing and deterministic `StrategyIdea` extraction, wired research-source APIs and Agent tasks, materialized seed drafts for funding carry / trend following / Paper-only grid-market-making, and added `paper-runs/{id}/step` to generate candidate Paper orders through the existing gatekeeper.
- **Files changed**: `shared/models/research_source.py`, `research_source/open_source_strategy_library/**`, `apps/api/routers/research_sources.py`, `apps/api/{main.py,routers/runs.py}`, `services/agents/service.py`, `services/execution/paper_signal.py`, `services/execution/__init__.py`, tests, and memory files.
- **Layer mapping**: `open_source_strategy_library` belongs to Data Layer E-level research data; extracted ideas/drafts belong to Strategy Layer; Paper order generation and review belongs to Execution Layer gatekeeper.
- **Research loop served**: Open-source project knowledge now enters as `StrategySourceManifest -> StrategyIdea -> StrategyDraft`; Paper signals continue through `BacktestRun -> PaperRun -> OrderExecution` with validation/risk checks.
- **Verification**: `py -3 -m pytest -q` -> 52 passed, 1 skipped; `py -3 -m ruff check .` passed; `py -3 -m mypy` passed; `npm --workspace frontend/admin run build` passed.
- **Notes**: GPL/AGPL sources remain research references only. Grid/market-making seeds are Paper-only in this tranche. Live framework integration, full repository cloning, and vector-store RAG remain future work.

### [TASK-015] Remediation plan first pass: engineering baseline, validation closure, ops visibility
- **Date**: 2026-07-03
- **Type**: fix + feat + docs
- **Summary**: Implemented the first remediation tranche without changing the six-layer architecture. Added package-boundary and Ruff/mypy baseline fixes; implemented carry walk-forward/OOS/stress validation reports; added system dependency health, exchange capabilities, and notification outbox APIs; made Makefile data/backtest targets real or explicitly failing; made unregistered Agent executors fail rather than falsely complete; and synchronized stale status docs.
- **Files changed**: `apps/__init__.py`, `pyproject.toml`, `apps/api/{config,main}.py`, `apps/api/routers/{backtests,market,notifications,system}.py`, `shared/models/{backtest,workflow,market,__init__}.py`, `services/validation/{walk_forward,report,stress_scenarios,__init__}.py`, `services/data/capabilities.py`, `services/notifications.py`, `services/agents/service.py`, `scripts/{data_check,data_sync,run_carry_backtest}.py`, `Makefile`, tests, status docs, and memory files.
- **Verification**: `py -3 -m pytest -q` -> 45 passed, 1 skipped; `py -3 -m ruff check .` passed; `py -3 -m ruff format --check .` passed; `py -3 -m mypy` passed; `npm --workspace frontend/admin run build` passed.
- **Notes**: Docker compose config was not locally verified because `docker` is not on PATH. The current folder is still not a Git repository, so publishing requires cloning/syncing to the original remote once GitHub network/auth are available.

### [TASK-014] Binance Data Layer first-tranche ingestion
- **Date**: 2026-07-03
- **Type**: feat
- **Summary**: Implemented the Data Layer tranche that makes the Paper console able to read real persisted Binance public market data. Added idempotent timeseries upserts, CCXT-backed OHLCV/funding backfill services, Binance WS payload normalization for closed Kline candles and funding updates, ingestion task execution for Binance backfill job types, and Vite `/api` proxy support for the admin console.
- **Files changed**: `services/data/{binance,repository,service,tasks,__init__}.py`, `infra/timescale/init.sql`, `frontend/admin/vite.config.js`, `pyproject.toml`, `tests/services/{test_binance_ingestion,test_timeseries_repository}.py`, project memory/status docs.
- **Verification**: Editable dev install passed; targeted Data Layer tests passed (`11 passed`); changed-file Ruff check passed; full `py -3 -m pytest -q` passed (`41 passed`); `npm --workspace frontend/admin run build` passed.
- **Notes**: Scope remains Binance public market data only. Live collector is a long-lived worker seam, not account sync or live trading execution.

### [TASK-013] Add Paper trading console and market overview APIs
- **Date**: 2026-07-03
- **Type**: feat
- **Summary**: Added market snapshot/OHLCV read APIs, console overview aggregation, Paper status update and RiskEvent acknowledgement endpoints, plus a real `frontend/admin` Paper Trading Console using `lightweight-charts`. The UI now shows Binance symbols, Kline panel, funding carry metrics, orders, positions, risk events, and manual Paper controls with explicit empty/error states.
- **Files changed**: `shared/models/{market,risk,workflow,__init__}.py`, `services/data/{repository,market,__init__}.py`, `services/strategy_library/repository.py`, `apps/api/{main.py,routers/{market,console,runs,risk}.py}`, `frontend/admin/{package.json,src/main.jsx,src/styles.css}`, `tests/api/test_console_market.py`.
- **Verification**: Targeted API tests passed; full `py -3 -m pytest -q` passed (`35 passed`); `npm --workspace frontend/admin run build` passed; Playwright desktop/mobile smoke passed with no mobile horizontal overflow.
- **Notes**: Browser smoke ran against frontend only, so API failure state was visible by design. Real WebSocket ingestion, exchange account sync, real order placement/cancel, notifications, and LLM veto remain not implemented.

### [TASK-012] Phase 1a/1b/1d/1e grounding implementation
- **Date**: 2026-07-03
- **Type**: fix + feat
- **Summary**: Restored `services/data`, fixed root-scoped ignore rules and runtime artifact ignores, moved LLM dependencies to optional extra, aligned compose Python images with Python 3.11, replaced carry placeholder metrics/cost constants with calculated net metrics and cost breakdown, added deterministic SignalEnsemble/MetaLabel service/API, and added MACD plus Dow swing trend technical signal modules. WorldQuant alpha semantics were explicitly deferred per user instruction.
- **Files changed**: `.gitignore`, `pyproject.toml`, `docker-compose.yml`, `services/data/**`, `services/validation/{carry,metrics,costs}.py`, `shared/models/{backtest,risk,signal,__init__}.py`, `services/strategy_library/{repository,ensemble/**,technical/**}`, `apps/api/{main.py,routers/ensemble.py}`, targeted tests, status docs, and project memory files.
- **Verification**: `py -3 -m pip install -e ".[dev]"` passed; `py -3 -c "import services.data; import apps.api.main"` passed; targeted Phase 1 tests passed (`14 passed`); full `py -3 -m pytest -q` passed (`31 passed`).
- **Notes**: `docker compose -f docker-compose.yml config` could not run because `docker` is not available on PATH. No git commit or `git rm --cached` was possible because `C:\Users\win\Desktop\AI--main` is not a Git repository in this environment.

### [TASK-011] Expand the persisted research loop with v1 APIs, gates, review writeback, and admin shell
- **Date**: 2026-07-02
- **Type**: feat
- **Summary**: Extended the platform beyond the first persisted carry slice. Added `/api/v1` envelopes and error handling, Alembic `0002`, persisted `RiskProfile` / `RiskEvent` / `ReviewReport` / `FailureRecord` / `AgentTask` / `LiveRun` / `OrderExecution` / `PositionSnapshot`, execution gatekeeper checks, review writeback, local alpha scanning into `StrategyIdea`, implementation-status reconciliation docs, compose overlays, Grafana dashboard scaffolding, and a React + Tailwind admin shell.
- **Files changed**: `apps/api/**`, `services/{agents,execution,review}/**`, `services/data/repository.py`, `services/strategy_library/{__init__,models,repository}.py`, `research_source/worldquant_adapter/**`, `migrations/versions/0002_expand_research_loop.py`, `docs/architecture/{implementation-status-matrix,technical-architecture-plan}.md`, `docs/ops/delivery-checklist.md`, `docker-compose*.yml`, `infra/{grafana,prometheus}/**`, `frontend/admin/**`, `tests/api/**`
- **Verification**: `py -3 -m pytest -q` -> 24 passed, 1 warning (`asyncio_mode` unknown because local environment still does not load `pytest-asyncio`); `npm install` + `npm run build` in `frontend/admin` passed.
- **Notes**: Ops overlays and Prometheus/Grafana assets are now in-repo scaffolds, but compose-level runtime validation is still pending. Walk-forward / DSR / stress-engine execution remains a Phase-1 gap.

### [TASK-010] Add persisted market-data carry backtest application flow
- **Date**: 2026-07-02
- **Type**: feat
- **Summary**: Added a real `CarryBacktestRequest` contract, a timeseries `DataRepository` for `ohlcv_bars` / `market_extras`, a `CarryBacktestApplicationService` that reads persisted Binance spot/perp/funding data and writes `BacktestRun`, plus a `/backtests/carry` API path and matching Celery task entrypoint.
- **Files changed**: `shared/models/{workflow.py,__init__.py}`, `services/data/{__init__.py,repository.py}`, `services/validation/{__init__.py,application.py,tasks.py}`, `services/strategy_library/repository.py`, `apps/api/{celery_app.py,routers/backtests.py}`, `tests/{conftest.py,api/test_vertical_slice.py,services/test_timeseries_repository.py,services/test_backtest_application.py}`
- **Verification**: `py -3 -m pytest -q` -> 22 passed; `POSTGRES_URL=sqlite:///./.verify_ai_quant.db py -3 -m alembic upgrade head` passed.
- **Notes**: Local Celery import smoke is still not verified on this machine because the Python environment does not currently have the `celery` package installed.

### [TASK-009] Implement the first persisted Binance carry vertical slice
- **Date**: 2026-07-02
- **Type**: feat
- **Summary**: Replaced the in-memory strategy, backtest, ingestion, and paper-run seams with SQLAlchemy repositories; added Binance top-universe ingestion helpers, carry backtest service, BTC/ETH-first paper-run defaults, Celery task entrypoints, repository-aligned Alembic migration, and end-to-end API tests for `StrategyIdea -> StrategyDraft -> Strategy -> StrategyVersion -> BacktestRun -> GateDecision -> PaperRun`.
- **Files changed**: `apps/api/routers/{strategies,backtests,ingestion,runs}.py`, `apps/api/celery_app.py`, `services/{database.py,data/**,validation/**,execution/**,strategy_library/**}`, `shared/models/{backtest.py,workflow.py}`, `migrations/versions/0001_create_strategies.py`, `tests/{conftest.py,api/**,repositories/**,services/**}`
- **Verification**: `py -3 -m pytest -q` -> 18 passed. Warning remains: local environment does not currently load `pytest-asyncio`, so `asyncio_mode` is reported as an unknown pytest option.
- **Notes**: The persisted slice now covers Binance top-universe ingestion job metadata, carry backtest persistence, and BTC/ETH-first paper-run preparation, but it still uses fallback universes and synchronous task bodies rather than live exchange execution.

### [TASK-001] Initialize governance, memory, and report-driven agent rules
- **Date**: 2026-06-28
- **Type**: bootstrap
- **Summary**: Created the repository governance layer first, including `AGENTS.md`, memory files, project metadata, and root configuration. The research report was elevated to the canonical architecture source for all future implementation.
- **Files changed**: `AGENTS.md`, `CLAUDE.md`, `.github/**/*`, `.gitignore`, `.env.example`, `README.md`
- **Notes**: This repository is still in Phase 0. Backend/frontend code should be added only after reading these governance files.

### [TASK-002] Add platform master design package
- **Date**: 2026-06-28
- **Type**: design
- **Summary**: Added the platform master design package as the repository's implementation mother document, including the main design document and three appendices for repository structure, feature phasing, and principles/non-goals.
- **Files changed**: `docs/architecture/platform-master-design.md`, `docs/architecture/appendix-*.md`, `docs/architecture/report-alignment.md`, `.github/agent/memory/*.md`
- **Notes**: The next recommended design step is the domain and interfaces design package.

### [TASK-003] Add domain and interfaces design package
- **Date**: 2026-06-28
- **Type**: design
- **Summary**: Added the domain and interfaces design package, defining the core domain objects, aggregate boundaries, lifecycle states, interface groups, agent I/O objects, and orchestration backbone for the research platform.
- **Files changed**: `docs/architecture/domain-and-interfaces-design.md`, `docs/architecture/platform-master-design.md`, `docs/architecture/report-alignment.md`, `.github/agent/memory/*.md`
- **Notes**: The next recommended design step is the data and ingestion design package.

### [TASK-004] Complete the remaining pre-development design assets
- **Date**: 2026-06-28
- **Type**: design
- **Summary**: Added the remaining pre-development preparation documents, including data and ingestion design, agent and orchestration design, execution/risk/review design, product specification, feature catalog, roadmap, environment/config guide, and delivery checklist.
- **Files changed**: `docs/architecture/*.md`, `docs/product/*.md`, `docs/roadmap/*.md`, `docs/ops/*.md`, `README.md`, `.github/agent/memory/*.md`
- **Notes**: The repository now contains a broad project preparation package. The next step can shift to concrete schema/API/model implementation.

### [TASK-005] Build Phase-0 global engineering scaffolding
- **Date**: 2026-06-29
- **Type**: feat (scaffolding, no business logic)
- **Summary**: Filled the foundational plumbing identified in the v2.0 集成方案 PDF gap analysis. Added `shared/models/` unified Pydantic contracts (OHLCVBar/MarketExtras/BacktestReport/GateDecision/RiskEvent/StrategyContract/TradeSignal/MacroEvent/AlphaPlan + enums); `infra/` (timescale `init.sql` hypertables, freqtrade config/strategies/user_data, jesse, grafana provisioning); docker-compose v2 (8 services, postgres→TimescaleDB) + `docker-compose.dev.yml`; expanded `.env.example` to 5 data tiers; Alembic + Strategy 18-field ORM + first migration `0001`; FastAPI strategies CRUD seam + `config.py` + `celery_app.py`; pyproject additions (alembic/anthropic/pandas-ta, ruff/mypy tightening); tests scaffolding (contract guard + API smoke); `Makefile`; `.pre-commit-config.yaml`; CI `ci.yml`; worldquant methodology seam (operators/parser/crypto factor generator stubs); docs reconciliation file.
- **Files changed**: `shared/**`, `infra/**`, `services/strategy_library/{models.py,__init__.py}`, `migrations/**`, `apps/api/{main.py,config.py,celery_app.py,routers/**}`, `docker-compose*.yml`, `.env.example`, `.gitignore`, `pyproject.toml`, `Makefile`, `.pre-commit-config.yaml`, `.github/workflows/ci.yml`, `research_source/worldquant_adapter/**`, `docs/architecture/v2-integration-reconciliation.md`
- **Verification**: py_compile 37 files OK; YAML/JSON validated; `shared.models` imports; contract tests 6/6 pass (caught + fixed a real bug: high≥low cross-field check needed `model_validator(mode="after")`). NOT run here (no docker/make/uv): `docker compose up`, `alembic upgrade head`, `uv lock`, API tests (need `pydantic_settings`). These run in CI / docker.
- **Notes**: Decisions: 移植 WorldQuant 方法论到加密（非搬运表达式）; v2.0 PDF 并入 docs 作落地细化（docx 仍为真源）. Next: P0-03 ohlcv_downloader + P0-12 strategies repository (replace in-memory seam).

### [TASK-008] Converge design sources, phase semantics, and API skeletons
- **Date**: 2026-07-02
- **Type**: feat + design convergence
- **Summary**: Implemented the “开发前整体设计收敛蓝图” in repo form. Added `docs/architecture/design-source-index.md` to fix the source-of-truth chain, Phase semantics, and document responsibilities; synchronized README / report-alignment / roadmap / product entry docs / project memory; coded `RiskProfile` plus workflow lifecycle contracts (`BacktestRun`, `PaperRun`, `LiveRun`, `ReviewReport`, `FailureRecord`, `IngestionJob`, `AgentTask`, `DecisionVetoResult`, strategy intake objects); expanded FastAPI from a single strategies seam to six interface-cluster skeletons; added `services/data` package scaffold; expanded `Settings` to cover `.env.example`; and updated tests to cover the new contracts and skeleton routes.
- **Files changed**: `docs/architecture/{design-source-index.md,report-alignment.md,appendix-b-feature-phasing.md,technical-architecture-plan.md}`, `docs/roadmap/phase-roadmap.md`, `docs/product/{product-spec.md,feature-catalog.md}`, `README.md`, `shared/models/**`, `apps/api/{main.py,config.py,celery_app.py,routers/**}`, `services/data/__init__.py`, `tests/{api,contracts}/**`, `.github/agent/memory/{project-memory.md,decisions-log.md,task-history.md}`
- **Verification**: `py -3 -m pytest -q` -> 11 passed; `shared.models` targeted import smoke passed; `apps.api.main` import smoke passed with 42 registered routes; compileall passed with a Windows path warning during directory listing but reported `COMPILE_OK`.
- **Notes**: Local environment still lacks `pytest-asyncio` / `pydantic_settings`; `apps/api/config.py` now contains a minimal fallback path for local smoke tests, while the primary dependency remains declared in `pyproject.toml`.
