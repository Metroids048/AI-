# Task History

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
