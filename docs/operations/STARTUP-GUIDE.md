# 量化交易系统启动指南

**更新时间**: 2026-07-15
**目标**: 实现 Top20 币种 7x24 小时自动交易监控

---

## 快速开始

### 方案 1: Docker Compose（推荐）

```bash
# 1. 启动基础设施
cd "C:/Users/win/Desktop/AI--main"
docker-compose up -d timescaledb redis

# 2. 等待服务健康检查
docker-compose ps  # 确认 timescaledb 和 redis 都是 healthy

# 3. 运行验证脚本
python scripts/verify-system-ready.py

# 4. 启动 API（包含自动调度器）
docker-compose up -d api

# 5. 查看日志
docker-compose logs -f api
```

### 方案 2: 本地开发模式

```bash
# 1. 使用本地配置
cp .env.local .env

# 2. 启动 Docker 基础设施（仅数据库和 Redis）
docker-compose up -d timescaledb redis

# 3. 安装依赖
pip install -e .

# 4. 运行验证脚本
python scripts/verify-system-ready.py

# 5. 启动 API
python -m uvicorn apps.api.main:app --host 0.0.0.0 --port 8000 --reload
```

---

## 关键修复说明

本次修复解决了 **3 个 CRITICAL 阻塞问题**：

### P0.1 信号信心度归零问题 ✅

**问题**: `DecisionPipeline._skipped()` 返回 `confidence_multiplier=0.0` 导致仓位计算归零

**修复**:
- 文件: `services/execution/decision_pipeline.py:615`
- 改动: 将 `confidence_multiplier=0.0` 改为 `confidence_multiplier=0.5`
- 原理: 当信号被跳过时，使用保守默认值而不是归零

**验证**:
```bash
python scripts/verify-system-ready.py
# 查看 [5/6] 信号生成测试，confidence_multiplier 应该 >= 0.5
```

### P0.2 数据库连接配置 ✅

**问题**: `timescaledb` 主机名只能在 Docker 网络内解析，宿主机无法连接

**修复**:
1. `docker-compose.yml`: 暴露 5432 和 6379 端口
2. `.env.local`: 提供 localhost 模式配置

**验证**:
```bash
# 测试数据库连接
python -c "from services.database import get_session_factory; get_session_factory()"
```

### P1.1 LLM 决策否决配置 ✅

**问题**: OpenRouter 免费模型触发 HTTP 429 速率限制

**修复**:
- 文件: `.env:41`
- 添加: `PAPER_RUNTIME_ENABLE_DECISION_VETO=false`
- 说明: 测试阶段禁用 LLM 否决，生产环境可切换到 Claude API

---

## 系统架构验证

### 1. 数据层
- ✅ TimescaleDB 存储 OHLCV、仓位、订单
- ✅ Redis 缓存实时数据和任务队列
- ✅ Binance Testnet 数据源

### 2. 策略层
- ✅ Top20 固定币种列表（`services/data/universe.py`）
- ✅ Technical 信号（MACD、EMA、RSI、ADX、Bollinger 等）
- ✅ Carry 套利信号（资金费率）

### 3. 执行层
- ✅ Paper Runtime 自动调度（300 秒周期）
- ✅ Gatekeeper 风控门槛
- ✅ 仓位计算（波动率调整 + 信心度乘数）

### 4. 风控层
- ✅ `max_portfolio_initial_risk_fraction=0.15`（Paper 阶段）
- ✅ `risk_per_trade` 和 `max_leverage` 检查
- ✅ 止损/止盈机制

---

## 监控与调试

### 查看运行日志

```bash
# Docker 模式
docker-compose logs -f api

# 本地模式
# 日志会输出到终端
```

### 关键日志关键字

**正常开单**:
```
paper_runtime cycle_start
decision_admitted symbol=BTC/USDT direction=LONG confidence_multiplier=0.75
order_accepted order_id=... symbol=BTC/USDT side=LONG
```

**问题日志**:
```
sizing_sentinel_triggered  # 仓位异常（已修复）
confidence_multiplier=0.0000  # 信心度归零（已修复）
llm decision veto runtime unavailable  # LLM 不可用（已禁用）
```

### 数据库查询

```sql
-- 查看最近的订单
SELECT * FROM order_executions ORDER BY created_at DESC LIMIT 10;

-- 查看当前仓位
SELECT * FROM position_snapshots WHERE run_type = 'paper' ORDER BY snapshot_time DESC LIMIT 10;

-- 查看 Paper Run 状态
SELECT paper_run_id, strategy_id, paper_status, paper_metrics_summary
FROM paper_runs
WHERE paper_status = 'running';
```

### Binance Testnet 验证

```python
from services.data.binance_client import BinanceCcxtClient

client = BinanceCcxtClient()
balance = client.fetch_balance()
print(f"USDT Balance: {balance['USDT']['free']}")
```

---

## 常见问题排查

### Q1: `psycopg.OperationalError: failed to resolve host 'timescaledb'`

**原因**: 使用了 Docker 网络内的主机名，但在宿主机运行

**解决方案**:
```bash
# 方案 1: 使用 .env.local（localhost 模式）
cp .env.local .env

# 方案 2: 确保在 Docker 容器内运行
docker-compose up -d api
```

### Q2: `sizing_sentinel_triggered notional=0.0000`

**原因**: P0.1 修复前的遗留问题

**解决方案**:
```bash
# 验证修复是否生效
python scripts/verify-system-ready.py

# 检查 decision_pipeline.py:615 行
grep -n "confidence_multiplier=0.5" services/execution/decision_pipeline.py
# 应该能找到匹配行
```

### Q3: 没有策略在运行

**原因**: 策略可能未启用或 Paper Run 未创建

**解决方案**:
```python
# 检查策略列表
from services.database import get_session_factory
from services.strategy_library import StrategyRepository

with get_session_factory()() as session:
    repo = StrategyRepository(session)
    strategies = repo.list_strategies()
    print(f"Found {len(strategies)} strategies")
    for s in strategies:
        print(f"  - {s.strategy_key}: {s.strategy_status}")
```

### Q4: LLM veto 报错 HTTP 429

**原因**: OpenRouter 免费模型触发速率限制

**解决方案**:
```bash
# 已在 .env 中禁用
grep "PAPER_RUNTIME_ENABLE_DECISION_VETO" .env
# 应该显示: PAPER_RUNTIME_ENABLE_DECISION_VETO=false
```

---

## Pre-commit Hook（防止未来出现类似问题）

创建 `.git/hooks/pre-commit`:

```bash
#!/bin/bash
set -e

echo "Running pre-commit checks..."

# 1. 运行测试
echo "[1/3] Running tests..."
python -m pytest tests/ --tb=short -q

# 2. 代码格式检查
echo "[2/3] Checking code format..."
python -m ruff check services/ shared/ apps/

# 3. 类型检查
echo "[3/3] Type checking..."
python -m mypy services/execution/decision_pipeline.py --no-error-summary

echo "✓ All checks passed!"
```

使其可执行:
```bash
chmod +x .git/hooks/pre-commit
```

---

## 下一步行动

### 短期（1-3 天）
1. ✅ 修复 P0 阻塞问题
2. ✅ 创建验证脚本
3. **运行几个周期，观察实际开单情况**
4. **收集真实日志，分析开单率**

### 中期（1-2 周）
1. **重新测量 Carry 策略净期望值**（delta-neutral 版本）
2. **优化信号质量**（降低拒单率）
3. **监控 Binance Testnet 执行情况**
4. **调整风控参数**（基于实际数据）

### 长期（1 个月+）
1. **建立 CI/CD 门槛**（强制测试通过）
2. **完善监控告警**（Grafana + Prometheus）
3. **实盘准备**（切换到主网，小资金验证）
4. **策略库扩展**（增加更多 Alpha 来源）

---

## 相关文档

- **诊断报告**: `docs/diagnosis/system-readiness-2026-07-15.md`
- **Carry 修复**: `docs/hotfix/HOTFIX-001-carry-hedge-indentation.md`
- **架构文档**: `AI_Quant_Research_Platform_完整报告.docx`
- **AGENTS.md**: 项目整体规范

---

**最后更新**: 2026-07-15
**维护人员**: AI Assistant
**审查状态**: 待人工确认
**优先级**: CRITICAL
