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

## ── 数据管理（首版本地入口）─────────────
data-sync:
	py -3 scripts/data_sync.py
data-check:
	py -3 scripts/data_check.py $(ARGS)

## ── 策略开发（carry lane 首版入口）───────────
backtest:
	py -3 scripts/run_carry_backtest.py $(ARGS)
backtest-all:
	@echo "Batch backtest is not implemented; run 'make backtest ARGS=\"...\"' per validated strategy."
	@exit 2
scan:
	@echo "VectorBT scanner is not implemented; use /api/v1/agents/tasks scan_local_alpha for research intake."
	@exit 2

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
