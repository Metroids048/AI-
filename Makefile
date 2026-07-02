# AI Quant Research Platform — unified command entry (PDF §5.2).
COMPOSE      = docker compose
COMPOSE_DEV  = docker compose -f docker-compose.yml -f docker-compose.dev.yml

.PHONY: up down logs ps build migrate migrate-new \
        data-sync data-check backtest backtest-all scan \
        test test-unit lint fmt lock sync memory-update

## ── 环境管理 ──────────────────────────────
up:            ## 启动全部服务（含 dev 覆盖）
	$(COMPOSE_DEV) up -d
down:
	$(COMPOSE) down
logs:
	$(COMPOSE) logs -f
ps:
	$(COMPOSE) ps
build:
	$(COMPOSE) build

## ── 数据库迁移 ────────────────────────────
migrate:       ## 应用 Alembic 迁移到最新
	$(COMPOSE) exec api alembic upgrade head
migrate-new:   ## 生成新迁移: make migrate-new m="message"
	$(COMPOSE) exec api alembic revision --autogenerate -m "$(m)"

## ── 数据管理（待实现，Phase 0）─────────────
data-sync:
	@echo "TODO P0-03: ohlcv_downloader.py 未实现"
data-check:
	@echo "TODO P0-04: gap_checker.py 未实现"

## ── 策略开发（待实现，Phase 0/1）───────────
backtest:
	@echo "TODO P0-08/P1-07: freqtrade_runner 未实现 (STRATEGY=$(STRATEGY))"
backtest-all:
	@echo "TODO: 全策略批量回测未实现"
scan:
	@echo "TODO P1-04: vectorbt_scanner 未实现 (STRATEGY=$(STRATEGY))"

## ── 测试与质量 ────────────────────────────
test:
	pytest -q
test-unit:
	pytest -q -m "not integration"
lint:
	ruff check . && ruff format --check . && mypy
fmt:
	ruff format . && ruff check --fix .

## ── 依赖锁定（需安装 uv）──────────────────
lock:
	uv lock
sync:
	uv sync --all-extras

## ── 记忆维护 ──────────────────────────────
memory-update:
	@echo "提醒：完工后更新 .github/agent/memory/ 三个文件"
