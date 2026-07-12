# Task History

### [TASK-053] Unblock directional Binance path: ghost positions + ReduceOnly loop
- **Date**: 2026-07-12
- **Type**: Execution repair + ops closeout
- **Summary**: Directional run was armed but opens died on `portfolio_initial_risk_exceeded` (Demo BTC/ETH injected into mature PaperRun) and close loops on `-2022 ReduceOnly` (local SOL ghost kept forever). Fixed exchange-already-flat close path, stopped injecting Demo positions into strategy runs (audit-only mirror), repaired local directional ghosts BTC/ETH/SOL. Ops closeout: engine on `:8016`, sole mature directional `457c6ecd` running, duplicate paused, Testnet open+close mirror proven (`directional_mirror_ok`).
- **Verification**: focused pytest 59 passed (`paper_runtime`/`mirror_lane`/`exit_ladder`/`gatekeeper`/`gateway`/`paper_runtime_api`); mirror proof LINK open `813722666` close `813722823`; API `/health` ok.
- **Limits**: Auto signal filters still sparse (no continuous auto gateway opens claimed); Top20 OOS / ExitLadder promotion still failed — no strategy/mainnet enablement.

### [TASK-052] Auto open/close gap plan Phases A–D
- **Date**: 2026-07-12
- **Type**: Execution / Validation
- **Summary**: (A) Confirmed directional run `457c6ecd` is armed but has **0** `gateway_order_id` — Testnet fills are mostly acceptance/carry, not directional DecisionPipeline (`docs/audits/2026-07-12-directional-binance-reachability.md`). (B) `_trace` now writes `strategy_lane=directional`; mirror gate tests added. (C) Replay supports `exit_mode=exit_ladder|fixed_2r`; Top20 comparison shows ExitLadder worse net expectancy than fixed 2R — no auto promotion (`docs/audits/2026-07-12-exitladder-replay-comparison.md`). (D) ATR% vol_low/mid/high tiers + weekly Celery refresh with core/standard fallback (ADR-055).
- **Verification**: focused pytest 18 passed (asset tiers, mirror lane, technical replay, celery schedule).
- **Limits**: Directional Testnet reachability still needs live cycle proof after arming; ExitLadder remains mechanical capability only; OOS gates still failed.

### [TASK-051] Desk Positions/Orders tabs now use Binance Demo as source of truth
- **Date**: 2026-07-12
- **Type**: Execution / frontend sync
- **Summary**: Operator saw Binance Demo Positions(3)/Open Orders(4) while desk bottom tabs showed 持仓 0 and local paper orders. Root cause: hero/account panels read `/binance-testnet-account`, but Positions/Orders tabs read local `overview` paper snapshots. Fixed by (1) mapping exchange positions/open+recent orders into desk tabs, (2) always polling Binance account, (3) writing exchange positions into audit+armed PaperRuns via `record_exchange_positions`, (4) probing algo open orders, (5) retrying false-flat first probe after API start, (6) deduping overview by symbol.
- **Verification**: API probe `open_position_count=3`, `open_orders=4` (STOP_MARKET), overview positions=3; frontend deskSync + useConsoleData tests passed.
- **Limits**: Conditional algo orders depend on `fapiPrivateGetOpenAlgoOrders`; public market REST 418 can still block auto readiness separately.

### [TASK-050] Fix desk data lock + ghost holdings display; prove Binance Demo opens
- **Date**: 2026-07-12
- **Type**: Execution / frontend ops repair
- **Summary**: Operator saw "服务不可用"/empty chart and uncleared ghost holdings/SL-TP overlays. Fixed sticky `useConsoleData` error that stopped all polling after API restarts; filtered chart SL/TP to non-rejected current-symbol orders; console overview now lists latest open positions per PaperRun instead of raw historical snapshot tail; Vite default proxy target `8016`. Opened real Binance Demo longs BTC/ETH/SOL for dual-platform proof (`gateway_order_id` filled). Remaining: public market REST hit HTTP 418 on some symbols so auto `execution_ready` can stay blocked by `data_stale` until ban cools.
- **Verification**: frontend MarketPanels/useConsoleData tests passed; overview positions `0`; Binance account `open_position_count=3` after trim; BTC OHLCV returns candles.
- **Limits**: Do not force-clear live `data_stale` while Binance public REST is 418-banned; auto cycles remain fail-closed.

### [TASK-049] Unblock Binance-sim auto trading: acceptance window, bootstrap clobber, reconcile CCXT
- **Date**: 2026-07-12
- **Type**: Execution / Ops repair
- **Summary**: Operator saw hours of no new Binance Demo fills and 10 local rejects (`portfolio_initial_risk_exceeded`). Root causes: (1) trading-status only scanned latest 50 AgentTasks so completed Top20 acceptance fell out of window; (2) bootstrap overwrote armed `cost_gate_verified`/`mirror` back to `paper_only`; (3) gateway reconcile crashed on CCXT `fetch_open_orders` without-symbol warning, so exchange-flat local ghosts never cleared; (4) portfolio initial-risk cap 5% vs 2%×N positions rejected after ~2 opens. Fixed query/bootstrap/reconcile/risk budget, re-armed funding+mature runs, cleared local open positions, restored `execution_ready=true`.
- **Layer mapping**: Execution/Risk owns gate + reconcile + arming; Validation admission unchanged; no mainnet.
- **Research loop served**: acceptance proof → armed sim mirror → exchange-flat reconcile → Top20 scan/decision evidence.
- **Verification**: targeted pytest 3 passed (`acceptance window`, `bootstrap preserve`, `reconcile survives open-orders warning`); live status `execution_ready=true`, blockers `[]`, both auto runs open positions `[]`, `binance_simulation_first` armed.
- **Limits**: Current 15m bar may show `skip_duplicate_cycle` until the next candle; continuous fills still require passing ensemble/MetaLabel/net-edge gates. Top20 OOS promotion remains failed.

### [TASK-048] ExitLadder + correlation tighten + reconcile decouple + Binance sim smoke
- **Date**: 2026-07-12
- **Type**: Execution Layer / Risk / audit
- **Summary**: Implemented multi-level ExitLadder for Paper (defaults in AUTO_PAPER_TECHNICAL_RULES), reduceOnly partial closes with fail-closed protection refresh on Binance sim, correlation discount + dual-peer reject, and exchange-flat reconcile independent of entry cycle_key. Added read-only sim smoke + boundary audit. Did not enable strategies or mainnet.
- **Layer mapping**: Execution owns ladder/runtime/gateway; Risk/Gatekeeper owns correlation admission; Review owns audit artifact. Validation admission unchanged.
- **Research loop served**: paper protection mechanics → optional testnet mirror → reconcile → review evidence. Strategy promotion still blocked by prior OOS failure.
- **Verification**: `pytest -q` -> `291 passed, 1 skipped`; Ruff passed; mypy passed on touched execution modules; `git diff --check` passed; `scripts/smoke_binance_simulation_path.py` connected (`testnet-fallback`, live_trading_enabled=false).
- **Limits**: Trailing ratchet remains break-even floor style (pre-existing); full ATR trail not in this slice. Smoke does not place new acceptance orders.

### [TASK-047] Complete fixed Top20 entry-policy prescreen against a one-hour baseline
- **Date**: 2026-07-12
- **Type**: Validation Layer / Data Layer / audit
- **Summary**: Corrected the offline comparison to use a reconstructed 1h baseline versus the current `4h direction -> 1h state -> 15m entry` policy. Both policies share fixed stoploss and fixed 2R takeprofit, making the report an entry-signal prescreen only. Added read-only historical slice caching and per-symbol multiprocessing so the full replay retains every 15m decision and every protective bar without interactive timeout.
- **Research loop served**: fixed Top20 OHLCV -> production DecisionPipeline replay -> cost-adjusted OOS/walk-forward metrics -> immutable audit evidence. No Strategy, PaperRun, Testnet, risk, or gateway configuration was changed.
- **Result**: 20 symbols, 15m/1h/4h, 2026-04-26 through 2026-07-12, no data gaps. Candidate signal density `1.0236x`; OOS net expectancy `-0.000005` versus baseline `0.001473`; Profit Factor `1.1036`; maximum drawdown `72.98%`. Prescreen failed and no promotion occurred.
- **Verification**: real replay completed in 298.9 seconds and wrote `docs/audits/2026-07-12-top20-technical-validation.md`; `pytest -q` -> `281 passed, 1 skipped`; Ruff passed; mypy passed for 122 files; `git diff --check` passed.
- **Limits**: This fixed-exit experiment isolates entry quality and is not evidence for the existing partial-profit/trailing state machine. Prompt 2-6 remain separate, user-confirmed slices.

### [TASK-046] Audit current behavior and add Strategy Library Playbook + ecosystem roadmap
- **Date**: 2026-07-12
- **Type**: audit / docs / Strategy Layer API / frontend / persistence
- **Summary**: Produced a code-line-backed current-state audit; researched and indexed six requested plus five new GitHub ecosystem sources; added typed Playbook contracts, code-derived scoped defaults, migration `0007`, audited roadmap persistence, dedicated API routes, and seven explanatory Strategy Library tabs while preserving the existing Strategy Assets CRUD lifecycle.
- **Layer mapping**: Strategy Layer owns Playbook definitions and source rules; Data/Research owns external-source manifests; Review owns roadmap audit evidence; frontend is an operator explanation/control surface only. Validation, Gatekeeper, Risk, and exchange execution authority are unchanged.
- **Research loop served**: external research -> structured source manifest -> operator Playbook/roadmap -> StrategyIdea/Draft -> future separately validated algorithm iteration. Roadmap status cannot directly materialize or execute a strategy.
- **Verification**: backend `257 passed, 1 skipped, 2 warnings`; frontend `11 files / 30 tests`; Ruff passed; mypy passed for 119 files; production build and diff check passed; clean SQLite migrated `0001 -> 0007`; desktop/mobile Chrome screenshots visually inspected; roadmap PATCH/GET persisted `in_progress` plus audit evidence.
- **Limits**: Playwright was unavailable, so browser QA used installed Chrome headless as the documented fallback. The pre-task full format gate remains historical debt (65 files) and was not mass-rewritten; only task-touched Python files pass format check. Bundle-size and two dependency deprecation warnings remain.

### [TASK-045] Harden strategy gates and complete bounded Top20 Futures Testnet acceptance
- **Date**: 2026-07-12
- **Type**: validation / execution safety / runtime evidence / frontend
- **Summary**: Added MetaLabel cold-start minimum history, explicit multi-timeframe fail-closed behavior, fixed-Top20 automatic Paper defaults, per-symbol Testnet evidence, a 120 USDT acceptance cap, sanitized preflight tooling, Celery worker-loss recovery semantics, per-task runtime timestamps, six-level Top20 frontend evidence, and a Temporal migration ADR without adding Temporal dependencies.
- **Layer mapping**: Strategy/Validation owns signal and sample sufficiency; Execution/Risk owns bounded Testnet orders, compensation and flat reconciliation; Ops owns Celery evidence; frontend only displays evidence; Review receives the audit artifact.
- **Verification**: Real Futures Testnet acceptance completed 20/20 symbols and 40 fills with zero final positions/orders. Final gates: backend `245 passed, 1 deselected`; Ruff passed; mypy passed for 115 files; admin Vitest `24 passed`; production build and npm high-severity audit passed; clean SQLite migration/runtime schema/API health passed.
- **Limits**: Spot Testnet credentials, Docker/Compose, two-hour Celery soak, pip-audit, and the ordinary Codex Security app scan remain environment/tooling dependent and must not be claimed as complete without fresh evidence.

### [TASK-044] Merge Codex branch into main and sync GitHub
- **Date**: 2026-07-12
- **Type**: git hygiene / sync
- **Summary**: Diagnosed push/pull confusion: local commits lived on `codex/fix-binance-top20-runtime` while `git push origin main` reported Everything up-to-date. Fast-forward merged 18 commits into `main`, pushed `130c174..3d754ee` to `origin/main`, deleted the local Codex branch. Repo policy going forward: work only on `main`.
- **Verification**: `git status` clean; `main` tracks `origin/main` at `3d754ee`.
- **Limits**: Remote Dependabot branches left untouched (dependency bumps, not Codex work). `gh` not authenticated for PR cleanup.

### [TASK-043] Implement Testnet Top20 acceptance, dual-leg funding carry, and the trading workbench
- **Date**: 2026-07-11
- **Type**: execution / risk / API / frontend / verification
- **Summary**: Added tiered automatic risk settings (BTC/ETH/SOL `10x` and `15%`; other fixed Top20 `5x` and `6%`) with equity-based notional sizing and unchanged global kill limits. Added a persisted Binance Futures Testnet acceptance API that sequentially opens and reduce-only closes all fixed Top20 symbols with protection, leverage setup, idempotency, compensation, and reconciliation evidence. Added an independent Binance Spot Testnet gateway and dual-leg carry state machine for Spot long plus Futures short. Rebuilt the Trading console into a chart/order/workspace layout, removed order-book requests/subscriptions, added fixed scrollable evidence tabs and structured connection failures, and retained real empty/loading/error/action states across the research modules.
- **Layer mapping**: Strategy Layer still owns signals; Validation Layer remains mandatory for strategy progression; Execution/Risk owns tier resolution, Testnet acceptance, Futures/Spot legs, protection, compensation, and reconciliation; Review/Ops owns evidence and operator visibility. The acceptance run is connectivity/execution proof only and cannot promote a strategy.
- **Research loop served**: `research idea -> strategy draft/contract -> backtest/optimization -> admitted Paper/Testnet strategy -> risk-controlled execution -> reconciliation -> review`, plus a separately auditable `carry plan -> Spot/Futures hedge -> close/compensate -> review` path.
- **Verification**: backend `232 passed, 1 skipped, 1 warning`; admin Vitest `9 files / 20 tests`; Vite production build passed; mypy passed for 114 source files; directed Ruff passed; `git diff --check` passed. Desktop and 390x844 Chrome screenshots confirmed the workbench layout and fixed record area render without incoherent overlap.
- **Limits**: External Binance acceptance was not executed because `127.0.0.1:7890` refuses connections and `SPOT_TESTNET_API_KEY` / `SPOT_TESTNET_API_SECRET` are not configured. Therefore there is no claim yet of 40 Futures fills, a completed BTC carry round trip, or official Binance Futures/Spot history screenshots. The unrelated Freqtrade asset manifest timestamp refresh remains preserved in the dirty worktree.

### [TASK-042] Repair Binance Mock auto execution, fixed Top20 visibility, reconciliation, and runtime logging
- **Date**: 2026-07-11
- **Type**: runtime remediation / execution safety / frontend / observability
- **Summary**: Replaced the stale July 9 API runtime with the current build, added safe legacy-SQLite migration recovery and global Agent Python startup, and made startup honor the operator's explicit Testnet auto-execution setting. Bootstrapping now keeps carry and mature-template runs active over the exact fixed Top20 with Binance simulation-first mirroring and pauses the duplicate legacy run. The market API returns all 20 candidates immediately with asynchronous exchange metadata enrichment; the frontend removed the fake three-symbol fallback and exposes monitoring/armed/no-signal/rejected states. Trading status includes build and effective execution state, and order sync reconciles all Top20 symbols. Logging now redacts wire/access details, suppresses only third-party websocket transport tracebacks, rotates at 10 MB with five retained files, and approximately 3.74 GB of authorized obsolete logs were removed. Optional external-source failures are task-level evidence and do not falsely mark the core scheduler unhealthy; Celery task exports are preloaded before scheduler worker threads start.
- **Layer mapping**: Data Layer owns fixed-universe metadata and exchangeInfo caching; Strategy/Validation retain signal and admission authority; Execution/Risk owns Mock-only arming, exchange-first persistence, idempotency, protection orders, and reconciliation; Review/Ops owns per-symbol rejection evidence and safe logs; frontend is visibility/control only.
- **Research loop served**: `Fixed Top20 market evidence -> strategy signal -> validation/gatekeeper/risk -> Binance Mock submission -> local order/position/protection persistence -> Top20 reconciliation/review`. Missing signal or failed gates terminate visibly without fabricated orders.
- **Verification**: backend `217 passed, 1 deselected, 1 warning`; full-repository Ruff passed; mypy passed across 110 source files; admin Vitest `8 files / 19 tests` and production build passed; `git diff --check` passed. Live probes showed scheduler running, Mock auto execution armed, live/mainnet false, two active runs scanning 20 symbols, a 20-item nonblocking universe response, and 20 order-sync summaries. Clean logs contained no API key header, signature/token query, third-party wire DEBUG, raw WebSocket frames, or traceback.
- **Limits**: No synthetic or threshold-relaxed order was created; current cycles had no strategy-qualified signal. Exchange metadata may initially display pending/unknown until background refresh completes, while gateway submission remains independently fail-closed. Current API credentials were retained per operator request but should be rotated because older local logs had contained request credentials.

### [TASK-041] Adversarial audit remediation and runtime verification
- **Date**: 2026-07-10
- **Type**: security / reliability / frontend remediation
- **Summary**: Closed the audit blockers without bypassing the six-layer gates: corrected the Paper status enum, made local SQLite startup run relational migrations plus its separately owned runtime tables, suppressed unsafe external wire logging, rejected withdrawal-enabled Binance keys, propagated stable client order IDs and timeout reconciliation, and rejected under-minimum opening notionals. The admin console now normalizes nullable auto-settings, uses POST for intelligence refresh, keeps multi-gateway rows uniquely keyed, and presents a stable API-unavailable state for both network errors and Vite proxy 5xx responses.
- **Layer mapping**: Data/ops owns local SQLite runtime schema; Execution/Risk owns exchange permission, idempotency, min-notional and unavailable-gateway fail-closed behavior; frontend is an operator surface only and does not bypass validation or risk gates.
- **Verification**: clean SQLite migration + schema + API lifespan `/health` returned 200; full Python `205 passed, 1 skipped`; Ruff passed; admin Vitest `8 files / 17 tests` and production build passed; desktop and 390px browser checks passed in the controlled standard local topology. API-down browser evidence showed the Chinese unavailable state and no new API requests after a six-second observation window.
- **Limits**: Docker/Compose runtime remains unverified because Docker is absent. Mypy currently fails with 68 errors in 23 files and is not a passing gate. Testnet credentials and all credential values remain absent from logs/memory; the Testnet key used during the preceding audit should be rotated by the operator.

### [TASK-040] Global adversarial test and architecture compliance audit
- **Date**: 2026-07-10
- **Type**: audit / QA
- **Summary**: Audited the current dirty worktree with offline adversarial harnesses, full regression, Binance Testnet open/reduce-only-close/reconciliation, and Playwright route checks. Added the evidence report at `docs/audits/2026-07-10-global-adversarial-architecture-audit.md`.
- **Findings**: Critical API lifespan crash from `paper_status="disabled"` not belonging to `RunStatus`; CCXT debug logging exposes authentication request material; API-key withdrawal permission self-check remains absent. High-risk gateway idempotency is not propagated to client order ids, and below-min-notional requests are silently increased to 50 USDT.
- **Verification**: Python `196 passed, 1 skipped`; targeted decision/risk tests `38 passed`; targeted LLM/gateway/API tests `23 passed`; admin Vitest `12 passed`; Mypy and admin build passed. Full Ruff fails on 3 existing errors in `scripts/_run_auto_cycles_verify.py`; Docker unavailable. API normal-data browser checks blocked by the confirmed startup failure.
- **Notes**: Testnet used `LIVE_TRADING_ENABLED=false`, `BINANCE_USE_TESTNET=true`, and an order cap below 120 USDT. No product code or existing tests were changed; temporary harnesses and raw logs are under ignored `.local/audit/`.

### [TASK-039] Paper auto-cycle proof + multi-timeframe OHLCV seed
- **Date**: 2026-07-09
- **Type**: fix + verification
- **Summary**: User reported no auto orders despite engine supposedly running 24/7. Root cause: directional lane uses 15m entry bars but heartbeat only maintained 1m; stale-data heartbeat created 92 blocking risk events; same-bar idempotency skipped most 60s cycles; carry lane correctly rejected funding arb with no net edge. Ran local verify script: manual open+close filled; after clearing risk events and seeding 15m/4h OHLCV, forced directional auto-cycle `opened=1`. Added `bootstrap_seed_multi_timeframe_ohlcv()` on API startup.
- **Files changed**: `services/execution/bootstrap.py`, `scripts/_run_auto_cycles_verify.py`, task-history.
- **Layer mapping**: Data Layer (OHLCV freshness) + Execution Layer (gatekeeper/risk events) + Validation (Paper cycles).
- **Verification**: `py -3 scripts/_run_auto_cycles_verify.py` -> manual OPEN/CLOSE filled, forced directional `opened=1`; `py -3 -m pytest tests/services/test_paper_bootstrap.py tests/services/test_paper_runtime.py -q` -> 8 passed.

### [TASK-038] Auto Paper/Testnet safety rollback after far conditional orders
- **Date**: 2026-07-09
- **Type**: fix + safety
- **Summary**: Investigated Binance Testnet far BTCUSDT conditional orders and found the unsafe path: local Paper bootstrap auto-enabled `mirror_to_gateway`, startup/default env forced `BINANCE_AUTO_EXECUTE=true`, relaxed signals were enabled locally, and Binance protection trigger prices were submitted without distance sanity checks. Changed automatic execution to explicit opt-in, disabled local relaxed/auto execution defaults, added Binance protection price validation before exchange entry submission, and stopped the old local FastAPI process on port 8000 so stale in-memory settings cannot keep cycling.
- **Files changed**: `shared/config.py`, `services/execution/{bootstrap,gateway,paper}.py`, `scripts/{start_paper_console,run-api-local}.ps1`, `scripts/bootstrap_and_verify_binance.py`, `.env.example`, local `.env`, and targeted tests.
- **Layer mapping**: Execution Layer / Risk Engine safety boundary. This does not change Strategy Layer signal semantics; it prevents Paper research cycles from becoming exchange actions without explicit operator consent.
- **Research loop served**: Strategy/Validation can continue producing Paper decisions, but Execution now fails closed before Testnet order placement when mirroring is not explicitly enabled or protection prices are invalid/far.
- **Verification**: `py -3 -m pytest tests/services/test_binance_gateway.py tests/services/test_paper_bootstrap.py tests/services/test_paper_runtime.py tests/api/test_paper_runtime_api.py tests/api/test_testnet_manual_trading.py -q` -> 28 passed / 1 warning; changed-file Ruff passed; `git diff --check` passed.

### [TASK-037] Market Intelligence capped factor vote
- **Date**: 2026-07-09
- **Type**: feature + data + strategy + frontend
- **Summary**: Implemented the approved Market Intelligence factor-voting plan without adding a seventh architecture layer. Added shared contracts for `MarketEvent`, `MarketIntelligenceFeatureSnapshot`, provider status, and capped `MarketIntelligenceSignal`; added Data Layer provider adapters/status for Binance/CoinGlass/CryptoQuant/DeFiLlama plus feature/signal scoring from existing market/news/macro/risk data; exposed `/api/v1/market-intelligence/{events,features,signals,refresh}`; wired the bounded vote into directional DecisionPipeline only after deterministic technical signals exist; added Trading/Ops/Review frontend visibility.
- **Files changed**: `shared/models/market_intelligence.py`, `services/data/market_intelligence.py`, `apps/api/routers/market_intelligence.py`, `services/execution/decision_pipeline.py`, `frontend/admin/src/{hooks/useConsoleData.js,components/RuntimePanels.jsx,pages/PaperConsole.jsx,pages/OpsConsole.jsx,pages/ReviewCenter.jsx}`, `.env.example`, targeted tests, and memory files.
- **Layer mapping**: Data Layer owns provider normalization/status and feature snapshots; Strategy Layer owns the capped intelligence vote; Agent Layer remains classification/explanation only; Execution/Risk still enforce MetaLabel, Decision Veto, Gatekeeper, stoploss, and Paper/Testnet boundaries; Review surfaces intelligence traces.
- **Research loop served**: `Provider/News/Macro/Risk evidence -> MarketIntelligenceSignal -> SignalEnsemble vote -> MetaLabel -> Decision Veto/Gatekeeper -> Paper/Testnet -> Review evidence`.
- **Verification**: targeted tests `23 passed`; changed Python Ruff passed; `py -3 -m mypy` passed; admin Vitest `12 passed`; admin build passed.
- **Notes**: CoinGlass/CryptoQuant are adapter-first and return `missing_credentials` until API keys are configured. The intelligence vote is schema-capped at `0.30` and cannot open a trade without a deterministic technical signal.

### [TASK-036] Real Binance Testnet open/close smoke and remaining quality baseline closure
- **Date**: 2026-07-09
- **Type**: live-testnet verification + fix + quality
- **Summary**: Executed a real Binance Futures Testnet BTCUSDT smoke through the gateway: opened `0.001` BTC with BUY market order `20356862614`, closed it with SELL reduce-only market order `20356874963`, then cleaned a pre-existing `0.0001` BTC residual position with SELL reduce-only order `20356888777`. Final probe confirmed `open_position_count=0` and no positions. Fixed gateway time sync for private Testnet calls and close-only side inversion before the smoke.
- **Files changed**: `services/execution/gateway.py`, `tests/services/test_binance_gateway.py`, quality cleanup across Ruff-reported files, `scripts/_testnet_open_close_report.json`, and memory files.
- **Layer mapping**: Binance gateway behavior belongs to Execution Layer; final flat-position verification is Risk Engine operational safety; local sanitized report is Review/Ops evidence.
- **Verification**: Real Testnet orders filled and are visible in Binance recent orders; full repo Ruff passed; `python -m pytest -q -m "not integration"` -> 185 passed, 1 deselected, 1 warning; `python -m mypy` passed; admin Vitest -> 12 passed; admin build passed; `npm audit --audit-level=high` -> 0 vulnerabilities; project `pip-audit .` -> no known vulnerabilities; `git diff --check` passed.
- **Notes**: Docker compose smoke is still blocked by host environment (`docker not found on PATH`), not by repository code. Profitability is still not claimed; this task proves exchange connectivity and safe open/close mechanics, not strategy edge.

### [TASK-035] Technical strategy hardening, Binance-first auto execution, and Strategy Library RAG
- **Date**: 2026-07-09
- **Type**: feat + fix + research assetization
- **Summary**: Hardened the existing carry + technical automatic strategy path. The technical lane now requires explicit indicator/price-action signals, supports 4h direction + 15m entry confirmation, adds RSI/EMA/ADX/VWAP/Bollinger and false-breakout logic, removes unsafe candle fallback, and lowers default risk sizing. Binance auto execution now fails closed when gateway submission fails. RAG now prioritizes `策略库/*.md`, and ABU research material is recorded as GPL-3.0 distilled research-only.
- **Files changed**: `services/execution/{decision_pipeline,bootstrap,paper_runtime,gateway,kill_switch}.py`, `services/strategy_library/technical/{indicators,price_action,__init__}.py`, `services/agents/rag_context.py`, `research_source/open_source_strategy_library/**`, `策略库/*.md`, frontend/admin runtime tests, and backend strategy/runtime tests.
- **Layer mapping**: Technical signal generation belongs to Strategy Layer; Binance-first order submission and fail-closed handling belong to Execution Layer/Risk Engine; RAG strategy documents belong to Data Layer E-level research intake feeding Strategy/Agent; failure/rejection codes feed Review Layer.
- **Research loop served**: `策略库/RAG -> Strategy signals -> DecisionPipeline -> Gatekeeper -> Binance Testnet/Paper order -> Review evidence` is now more explicit and auditable.
- **Verification**: `python -m pytest -q -m "not integration"` -> 184 passed, 1 deselected, 1 warning; changed-file Ruff passed; `python -m mypy` passed; admin Vitest passed (12 tests); admin build passed; `git diff --check` passed.
- **Notes**: Full repo Ruff still reports 33 pre-existing unrelated style issues in older files; no real Binance Testnet order was submitted during this session; profitability is not claimed and still requires backtest/OOS/Paper evidence before live promotion.

### [TASK-034] LLM free-model chain + carry/directional dual PaperRun lanes
- **Date**: 2026-07-09
- **Type**: feature + execution + agent
- **Summary**: Wired OpenRouter + GitHub Models free-model fallback via unified `build_configured_llm_runtime()`; fixed Anthropic quota errors to participate in fallback chain; added lightweight RAG snippets for decision veto prompts; enabled `PAPER_RUNTIME_ENABLE_DECISION_VETO` in local startup; split auto Paper into carry (`auto_paper_btc_funding`, FundingArbitrageSignal admission) and directional (`auto_paper_btc_technical`, DecisionPipeline + LLM veto) Top20 runs with `strategy_lane`; carry rejections no longer block protective closes via false LLM veto.
- **Files changed**: `.env`, `.env.example`, `services/agents/{llm_factory,rag_context,llm_runtime,__init__}.py`, `services/execution/{decision_pipeline,paper_signal,paper_runtime,bootstrap}.py`, `services/data/news.py`, `apps/api/routers/agents.py`, `scripts/{run-api-local,start_paper_console}.ps1`, `frontend/admin/src/components/RuntimePanels.jsx`, `tests/services/{test_llm_dual_lane,test_paper_bootstrap}.py`.
- **Layer mapping**: Agent Layer (LLM runtime + RAG veto context); Execution Layer (dual-lane PaperRun + carry admission); Strategy Layer (lane-specific bootstrap rules).
- **Verification**: `py -3 -m pytest -q -m "not integration"` -> 179 passed, 1 deselected; targeted LLM/carry/bootstrap tests 22 passed; `DecisionDebugPanel` Vitest passed. Pre-existing `TradingConsolePanels.test.jsx` failures unchanged (unrelated copy assertions).

### [TASK-033] Report gap closure — frontend depth, metrics, compose smoke, signal continuity
- **Date**: 2026-07-08
- **Type**: feature + refactor + ops
- **Summary**: Closed the external review gaps after TASK-030/031/032: moved pytest SQLite artifacts to `.local/test-runtime/` and cleaned 46 stale root `.db` files; added RiskProfile PUT API + frontend create/edit forms; expanded Validation/Research/Strategy routes with detail pages, backtest submit, research-source refresh/extract, draft promote/materialize, and strategy status updates; exposed `/metrics` (scheduler + LiveFeedBus gauges) and wired Prometheus scrape; added `scripts/compose_smoke.py` plus optional `.github/workflows/compose-smoke.yml`; implemented MACD/Dow continuous strength with confidence-scaled ensemble weights.
- **Files changed**: `tests/conftest.py`, `.gitignore`, `scripts/{clean_test_artifacts,compose_smoke}.py`, `shared/models/risk.py`, `services/strategy_library/repository.py`, `apps/api/routers/{risk,strategies,backtests,metrics}.py`, `apps/api/{main,auth}.py`, `services/strategy_library/technical/{macd,dow_trend}.py`, `services/execution/decision_pipeline.py`, `infra/prometheus/prometheus.yml`, `frontend/admin/src/{router.jsx,styles.css,pages/*,components/*}`, `.github/workflows/compose-smoke.yml`, tests + memory files.
- **Layer mapping**: Validation/Strategy/Risk frontend depth (Validation + Strategy + Risk layers); signal continuity (Strategy + Execution); `/metrics` + compose smoke (Ops/Engineering).
- **Verification**: `py -3 -m pytest -q -m "not integration"` -> 167 passed, 1 deselected; `npm --workspace frontend/admin run test -- --run` -> 12 passed; `npm run admin:build` passed. Compose smoke not run locally (Docker not on PATH); CI optional workflow added.

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

### [TASK-030] Security closure, Docker scheduler guard, reconnect visibility, and third-party-backed frontend pages
- **Date**: 2026-07-07
- **Type**: fix + security + frontend + ops
- **Summary**: Closed TASK-029 residuals and extended frontend data wiring. Upgraded Python/Node audit baselines, fixed Docker duplicate-scheduler risk with explicit Celery mode and compose validation, wired Binance WS reconnect errors into `LiveFeedBus`, replaced Validation/Review/Research/Ops placeholders with real API-backed pages, and added fail-soft `refresh=true` third-party read-through for news and macro endpoints.
- **Files changed**: `pyproject.toml`, `.github/workflows/ci.yml`, `docker-compose.{paper,live}.yml`, `scripts/compose_validate.py`, `services/data/binance.py`, `services/execution/scheduler.py`, `apps/api/routers/market.py`, `frontend/admin/**`, targeted tests, and `docs/security/task-030-security-scan.{md,html}`.
- **Layer mapping**: Data Layer owns third-party news/macro refresh and Binance WS state; Execution Layer owns scheduler mode separation and reconnect observability; Validation/Review/Research/Ops frontend pages expose existing layer data without becoming decision makers; CI/Ops owns supply-chain gates.
- **Research loop served**: Third-party Data inputs now flow into Review/Research visibility; Validation signals and hypothesis/backtest state are inspectable; Execution scheduler/feed state is visible; failures still route through Review memory rather than bypassing risk controls.
- **Verification**: `py -3 -m pytest -q` -> 149 passed, 1 skipped; `py -3 -m ruff check .` passed; `py -3 -m mypy` passed; changed-file Ruff format check passed; admin Vitest passed; admin build on Vite 8.1.3 passed; `npm audit --audit-level=high` passed; `py -3 -m pip_audit . --progress-spinner off --timeout 30` passed; `py -3 scripts/compose_validate.py` skipped because Docker is not on PATH.
- **Notes**: Whole-machine `pip_audit` still reports non-project global packages (`litellm`, `nltk`, `torch`); these are outside this repo's dependency graph and were not upgraded/removed. Full repo format check still reports historical formatting drift in files outside this change set.

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

### [TASK-032] Protective exits, free LLM fallback, and Binance Testnet mirror
- **Date**: 2026-07-08
- **Type**: feat + refactor + verification
- **Summary**: Implemented the pasted plan for stoploss/takeprofit automatic closing, `trail_after_r` trailing-stop ratchet, free-model LLM fallback routing, and explicit Binance Futures Testnet mirroring for automatic Paper runtime fills.
- **Files changed**: `services/execution/paper_runtime.py`, `services/strategy_library/repository.py`, `services/agents/llm_runtime.py`, `services/execution/decision_pipeline.py`, `services/data/news.py`, `apps/api/{config.py,routers/runs.py}`, `frontend/admin/src/{pages/PaperConsole.jsx,components/TradingConsolePanels.jsx}`, `.env.example`, targeted tests, and memory files.
- **Layer mapping**: Protective exits and gateway mirroring belong to Execution Layer / Risk Engine; `FailureRecord`/iteration writeback belongs to Review Layer; OpenRouter/GitHub Models runtime belongs to AI Agent Layer; the explicit PaperRun mirror switch supports Validation Layer's real-market Paper/Testnet observation without bypassing Gatekeeper.
- **Research loop served**: `Strategy rules -> Paper signal -> Gatekeeper -> Paper fill -> protective close / Testnet mirror -> FailureRecord or iteration_history -> Review reuse` is now auditable, while LLM classification/veto can use free providers and still fail closed.
- **Verification**: Targeted Paper/LLM/API tests passed (`13 passed`); full `py -3 -m pytest -q` passed (`162 passed, 1 skipped`); `py -3 -m ruff check .`, changed-file `ruff format --check`, `py -3 -m mypy`, admin Vitest (`9 passed`), admin build, `npm audit --audit-level=high`, `py -3 -m pip_audit . --timeout 60`, and `git diff --check` passed. `scripts/compose_validate.py` skipped because Docker is not on PATH.
- **Notes**: No user secrets were written to tracked files. Real private Binance Testnet order appearance still requires operator-provided local `.env` credentials and a live service run with `mirror_to_gateway=true`.

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
### [TASK-039] Fixed Top20 Binance simulation-first auto-trading optimization
- **Date**: 2026-07-10
- **Type**: feat + safety hardening + frontend
- **Summary**: Implemented the fixed operator Top20 universe, Binance symbol/status mapping including `PEPE -> 1000PEPEUSDT`, all-20 heartbeat refresh, mature-template default auto strategy, disabled operator-experience 4h/15m research lane, medium-risk defaults, typed auto-settings API, Binance simulation-first local sync, order-sync API, and Trading console panels for auto settings, Top20 monitoring, message sources, and order reconciliation.
- **Files changed**: `services/data/{universe,binance,market,service,tasks}.py`, `services/execution/{bootstrap,decision_pipeline,paper_runtime}.py`, `apps/api/routers/{market,runs}.py`, `shared/models/{market,risk,workflow,__init__}.py`, `frontend/admin/src/{hooks/useConsoleData.js,pages/PaperConsole.jsx,components/RuntimePanels.jsx}`, and targeted backend/frontend tests.
- **Verification**: Targeted backend tests passed (`24 passed` before final smoke); full non-integration pytest passed (`196 passed, 1 deselected, 1 warning`); Ruff, mypy, admin Vitest (`12 passed`), admin build, `git diff --check`, and Playwright trading-page smoke passed. Browser smoke was run with Vite only, so API proxy 502s were expected; no JS runtime crash remained after adding the missing `asArray` helper.
- **Notes**: Live/mainnet trading remains disabled. The user-provided report Markdown remains untracked and was not modified.

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

## 2026-07-12 — Local console reliability, cost gate, and audit retention

- **Summary**: Separated Testnet acceptance fills from strategy performance, persisted Binance demo account snapshots and reconciled orders, fixed funding carry edge calculation to deduct four execution legs, and localized the trading workspace. The desktop console now reads persisted data on API port `8016`; an external scheduler process refreshes markets and runs automatic Paper cycles without blocking the API.
- **Verification**: API core reads returned 200 in 1-261 ms; local overview retained 10 orders, 10 positions, and 2 account snapshots; scheduler reported running and auto execution armed. Full suite `253 passed, 1 skipped`; Ruff, frontend `26` tests, frontend production build, and `git diff --check` passed.
- **Notes**: Binance demo account API remains network/credential dependent. Its failure is isolated to the account panel and does not mark the entire console unavailable. Mainnet remains disabled; cost gate still prevents automatic Testnet mirroring until explicit validation evidence exists.

## 2026-07-08 — Binance Testnet gateway + local paper mirror E2E

- **Summary**: Fixed CCXT Binance USDM Testnet integration (manual testnet URLs instead of deprecated `set_sandbox_mode`), algo protection orders (`algoType=CONDITIONAL`), paper mirror min-notional bump (50 USDT), and dev relaxed signal filters. Verified live K-line feed, testnet balance sync (~5256 USDT), manual testnet orders, and paper auto-cycle mirror (`gateway_order_id` on recent fills).
- **Files changed**: `services/execution/{gateway.py,paper_runtime.py,decision_pipeline.py,paper_signal.py}`, `shared/config.py`, `apps/api/celery_app.py`, `.env.example`, `tests/services/test_binance_gateway.py`
- **Verification**: `py -3 -m pytest tests/services/test_binance_gateway.py tests/services/test_paper_bootstrap.py -q` -> 4 passed; API `trading-status` credentials+gateway+scheduler+live feed OK; testnet manual order `gateway_order_id=20127948601`; paper mirror orders `20128622805` / `20128804499`.

## 2026-07-12 — Fixed Top20 directional Paper execution and cost-risk controls

- **Summary**: Reworked the automatic directional Paper lane around `4h trend -> 1h state -> 15m entry -> 1m protection`. The fixed Top20 remains intact; heartbeat now maintains all four closed-bar timeframes. Existing positions receive 1m stop, time-exit, partial-profit, and drawdown protection even when entry bars are stale or already processed.
- **Risk policy**: BTC/ETH/SOL use 20x caps; the other fixed Top20 symbols use 10x caps. Position size remains stop-distance risk based at 2% per trade, with a 5% aggregate initial-stop-risk cap, 5% daily new-entry halt, and 20% hard-drawdown close-and-lock action. All work remains Paper-only.
- **Exit/cost policy**: +1R break-even ratchet, +2R partial 50% exit, trailing remainder, and 24-hour exit below +0.5R. Core assets model 10bps per side; other symbols 18bps per side, with the existing transaction-cost ledger retaining fee/slippage evidence.
- **LLM boundary**: LLM/RAG remains structured audit and research input. Only persisted high/critical risk events hard-veto entries; model budget/runtime/schema failures are recorded as advisory unavailability and cannot weaken deterministic gates.
- **Verification**: `pytest -q` -> 274 passed, 1 skipped; `ruff check .`; `mypy`; `npm --workspace frontend/admin run build`; `git diff --check` all passed. Vite retains its pre-existing >500kB bundle warning.
- [project: C:\Users\Windows11\Desktop\量化项目] 2026-07-12 Fixed Top20 Binance Testnet acceptance connected end-to-end
  - **内容**: 先定位到直接调用 `TestnetAcceptanceService` 不会写回平台验收状态的断点；随后通过正式 API `/api/v1/execution/testnet-acceptance-runs` 执行固定 20 币顺序开仓/平仓验收。
  - **验收证据**: `run_status=completed`、20/20 币完成、40 笔成交、无失败币、`final_open_position_count=0`、`final_open_order_count=0`；API `trading-status` 已回写 `testnet_acceptance_verified=true`、`auto_execution_state=ready`、`execution_ready=true`、调度器运行中。
  - **边界**: 仅 Binance Futures Testnet/Mock，主网保持关闭；预检确认账户初始 0 持仓/0 挂单。正式验收 run id: `02c25bdd-be98-4060-a816-e625c61e7b24`。
- [project: C:\Users\Windows11\Desktop\量化项目] 2026-07-12 Trading console strategy/audit boundary and browser sync fix
  - **结论**: 固定 Top20 的 40 笔 Binance Testnet 成交属于 `testnet_acceptance` 基础设施验收，不属于量化策略收益或策略开平逻辑；自动 Paper 周期按既定资金费成本门禁拒绝当前信号，未产生策略新仓。
  - **故障**: 本地启动脚本将行情接口强制置为 API-only，外部行情读取超时会污染整页错误；更关键的是 CORS `OPTIONS` 预检被 Bearer 认证中间件拦截，浏览器拿不到任何数据，命令行带 Token 却正常。
  - **修复/验证**: 本地控制台继续使用隔离调度器缓存；行情可选请求失败不再升级成全局错误；认证中间件放行 `OPTIONS`，启动器注入 `http://127.0.0.1:5173`/`localhost:5173` CORS 白名单。预检返回 200，后端健康测试 8 passed，前端 Vitest 30 passed，API overview/market endpoints 全部 200。
  - **同步边界**: 账户 API 已能读取 Binance 最近订单与 0 持仓；主交易表展示本地执行/审计记录，策略订单与验收审计继续分开，避免把验收单计入策略表现。
