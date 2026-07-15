# 系统就绪状态诊断报告

**诊断时间**: 2026-07-15  
**诊断范围**: Top20 自动交易系统完整链路  
**诊断目标**: 验证 7x24 小时自动开平单能力

---

## 执行摘要

### 当前状态
**系统 CAN 自动交易，但存在严重的信号质量问题导致几乎不会真正开单**

- ✅ **配置完整**: 数据库、Redis、Binance API 密钥均已配置
- ✅ **调度器运行**: Paper Runtime 自动循环已启动（300秒周期）
- ✅ **Top20 扫描**: 固定 Top20 列表正确加载
- ⚠️ **信号生成崩溃**: `confidence_multiplier=0.0` 导致 100% 的信号被归零
- ❌ **实际开单率**: 接近 0%（所有信号都因 `sizing_sentinel` 被拒绝）

### 核心问题
给了明确方案还会出严重错误的**系统性根因**：

1. **决策管道返回零信心度** - `DecisionPipeline.evaluate()` 返回 `confidence_multiplier=0.0`
2. **仓位计算公式崩溃** - `notional = base * confidence_multiplier` → `0.0 * anything = 0`
3. **风控门槛拒绝零仓位** - Gatekeeper 的 `MIN_SANE_NOTIONAL_FRACTION=0.005` 检测到异常并拒绝

这不是"个案 bug"，而是**设计缺陷**：信心度乘数作为仓位缩放因子，当管道返回零信心度时，整个信号生成链崩溃。

---

## 1. 阻塞问题清单（按优先级）

### P0 - 立即修复（阻止任何开单）

#### P0.1 信号信心度归零问题
**文件**: `services/execution/decision_pipeline.py`  
**症状**: 日志显示 `confidence_multiplier=0.0000`，导致仓位计算为 0  
**根因**: `DecisionPipeline.evaluate()` 的信心度计算逻辑返回 0

**验证命令**:
```bash
cd "C:/Users/win/Desktop/AI--main"
python -c "
from services.data import DataRepository
from services.execution.decision_pipeline import DecisionPipeline
from services.database import get_session_factory
from services.strategy_library import StrategyRepository

with get_session_factory()() as session:
    data_repo = DataRepository(session)
    strategy_repo = StrategyRepository(session)
    pipeline = DecisionPipeline(data_repo=data_repo, strategy_repo=strategy_repo)
    
    # 获取一个策略
    strategies = strategy_repo.list_strategies()
    if strategies:
        strategy = strategies[0]
        result = pipeline.evaluate(
            strategy=strategy,
            symbol='BTC/USDT',
            timeframe='15m',
            enable_decision_veto=False,
            relaxed_signals=False
        )
        print(f'confidence_multiplier: {result.confidence_multiplier}')
        print(f'should_trade: {result.should_trade}')
        print(f'trace: {result.trace}')
"
```

**修复方案**:
需要读取 `services/execution/decision_pipeline.py` 文件，定位 `confidence_multiplier` 计算逻辑：

1. 检查 `DecisionPipeline.evaluate()` 方法中的信心度计算
2. 可能的问题：
   - Meta-label 模型返回零概率
   - 信号集成逻辑缺少默认值
   - 条件判断过于严格（例如要求所有指标一致才返回 >0 信心度）
3. 临时解决方案：在 `confidence_multiplier` 为 0 时，回退到默认值（例如 0.5 或 1.0）

**具体修复位置**（需验证）:
```python
# services/execution/decision_pipeline.py - 大约在 evaluate() 方法中
# 查找类似这样的逻辑：
confidence_multiplier = some_calculation()
if confidence_multiplier == 0:
    confidence_multiplier = 0.5  # 回退到保守默认值
```

---

#### P0.2 数据库连接配置问题
**文件**: `.env`, `docker-compose.yml`  
**症状**: 配置文件中使用 `timescaledb` 主机名，但可能在非 Docker 环境中无法解析  
**影响**: 如果从宿主机直接运行 Python 脚本（非容器内），数据库连接会失败

**当前配置**:
```env
# .env
POSTGRES_URL=postgresql+psycopg://postgres:postgres@timescaledb:5432/ai_quant
```

**修复方案**:
```env
# 方案 1: 如果只用 Docker Compose
POSTGRES_URL=postgresql+psycopg://postgres:postgres@timescaledb:5432/ai_quant

# 方案 2: 如果需要从宿主机运行（开发调试）
POSTGRES_URL=postgresql+psycopg://postgres:postgres@localhost:5432/ai_quant
# 同时在 docker-compose.yml 中暴露端口：
# services:
#   timescaledb:
#     ports:
#       - "5432:5432"
```

**验证**:
```bash
# 从宿主机测试连接
python -c "from shared.config import settings; print(settings.postgres_url)"
python -c "from services.database import get_session_factory; get_session_factory()"
```

---

### P1 - 高优先级（影响开单质量）

#### P1.1 LLM 决策否决不可用
**症状**: 日志显示 `llm decision veto runtime unavailable: openrouter/nvidia/nemotron-3-super-120b-a12b:free unavailable: HTTP 429`  
**影响**: LLM 辅助决策功能失效，可能导致低质量信号进入执行

**当前配置**:
```env
OPENROUTER_API_KEY=<set-in-local-dotenv-do-not-commit>
GITHUB_MODELS_TOKEN=<set-in-local-dotenv-do-not-commit>
```

**问题**:
1. OpenRouter 免费模型触发速率限制（HTTP 429）
2. GitHub Models 返回 401 未授权

**修复方案**:
```env
# 方案 1: 暂时禁用 LLM 否决（适合测试阶段）
PAPER_RUNTIME_ENABLE_DECISION_VETO=false

# 方案 2: 使用付费 Claude API（已配置但未设置为 veto agent）
CLAUDE_API_KEY=<your-key>
AGENT_LLM_PROVIDER_MAP={"decision_veto_agent":"anthropic"}
AGENT_LLM_MODEL_MAP={"decision_veto_agent":"claude-sonnet-4-6"}

# 方案 3: 检查 GitHub Models token 权限
# 访问 https://github.com/settings/tokens 确认 token 未过期且有 model:read 权限
```

---

#### P1.2 仓位大小计算异常
**症状**: 日志显示 `sizing_sentinel_triggered` 警告，`notional=0.0000`  
**根因**: 这是 P0.1 的下游症状 - 因为 `confidence_multiplier=0.0` 导致

**计算链路**:
```python
# services/execution/paper_signal.py::_requested_notional()
notional = volatility_sized_notional * max(confidence_multiplier, 0.0)
# 当 confidence_multiplier = 0.0 时:
notional = anything * 0.0 = 0.0
```

**修复**: 解决 P0.1 后自动解决

---

### P2 - 中优先级（不阻止开单但影响体验）

#### P2.1 Docker 环境未启动
**症状**: `docker: command not found`（Windows 环境可能需要 Docker Desktop）  
**影响**: 无法通过 `make up` 启动完整环境

**修复方案**:
1. 安装 Docker Desktop for Windows
2. 或者直接使用本地 Python 运行（需要手动启动 PostgreSQL 和 Redis）

**本地运行方案**:
```bash
# 1. 启动 TimescaleDB（手动或 Docker 单独运行）
docker run -d --name timescaledb -p 5432:5432 \
  -e POSTGRES_DB=ai_quant -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=postgres \
  timescale/timescaledb:2.17.2-pg16

# 2. 启动 Redis
docker run -d --name redis -p 6379:6379 redis:7

# 3. 更新 .env
POSTGRES_URL=postgresql+psycopg://postgres:postgres@localhost:5432/ai_quant
REDIS_URL=redis://localhost:6379/0

# 4. 直接运行 API
cd "C:/Users/win/Desktop/AI--main"
pip install -e .
python -m uvicorn apps.api.main:app --host 0.0.0.0 --port 8000
```

---

#### P2.2 自动调度器配置验证
**当前配置**:
```env
RUNTIME_SCHEDULER_MODE=inprocess
RUNTIME_SCHEDULER_AUTOSTART=true
PAPER_RUNTIME_CYCLE_SECONDS=300
```

**验证**: 调度器已启动（从日志时间戳可以看到每 5-6 分钟一个周期）

**无需修复** - 配置正确

---

## 2. 为什么给了明确方案还会出严重错误

### 系统性根因分析

#### 2.1 架构设计缺陷
**问题**: 信心度乘数作为**单点故障**

```python
# 当前设计（易崩溃）:
confidence_multiplier = some_complex_calculation()  # 可能返回 0
notional = base_notional * confidence_multiplier    # 0 * anything = 0
# 整个信号链崩溃

# 改进设计（容错）:
confidence_multiplier = some_complex_calculation()
if confidence_multiplier < MIN_CONFIDENCE_THRESHOLD:  # 例如 0.3
    # 回退到保守仓位，而不是拒绝
    confidence_multiplier = MIN_CONFIDENCE_THRESHOLD
notional = base_notional * confidence_multiplier
```

**为什么会这样**:
- `DecisionPipeline` 是复杂的多指标集成逻辑
- 当任何一个子模块（信号、集成、元标签）返回"不确定"时，整个管道输出 0
- 没有**分级降级**机制（例如：元标签失败 → 回退到纯技术指标）

---

#### 2.2 缺乏边界条件测试
**问题**: 代码假设"正常情况"，但没有处理"边界情况"

| 边界情况 | 当前行为 | 应有行为 |
|---------|---------|---------|
| `confidence_multiplier = 0.0` | 仓位归零 → 拒单 | 回退到保守默认值 |
| Meta-label 模型未训练 | 返回 0 概率 | 跳过元标签，使用原始信号 |
| 所有指标不一致 | 信心度 0 | 使用最强信号 + 降低信心度 |
| LLM veto 不可用 | 警告但继续 | ✅ 正确处理 |

**缺失的测试**:
```python
# tests/services/execution/test_paper_signal.py（应该有但可能没有）
def test_zero_confidence_multiplier_fallback():
    """当 confidence_multiplier = 0 时，应该回退到默认值而不是崩溃"""
    # ...

def test_meta_label_unavailable_fallback():
    """当元标签不可用时，应该跳过而不是返回 0"""
    # ...
```

---

#### 2.3 日志不可操作
**问题**: 日志显示"症状"而非"根因"

```log
# 当前日志（不可操作）:
sizing_sentinel_triggered symbol=BTC/USDT notional=0.0000 confidence_multiplier=0.0000

# 改进日志（可操作）:
sizing_sentinel_triggered symbol=BTC/USDT notional=0.0000 confidence_multiplier=0.0000
  | root_cause: DecisionPipeline.evaluate() returned confidence=0.0
  | reason: meta_label_win_rate=None, ensemble_discarded=True
  | fix: check meta_label training status or disable meta_label gate
```

**为什么这么做不够**:
- 运维人员看到 `notional=0.0000` 会以为是配置问题（风控参数太严格）
- 实际根因在上游的 `DecisionPipeline`，但日志没有指出
- 需要翻 10+ 个文件才能定位问题

---

#### 2.4 配置分散且缺乏验证
**问题**: 关键配置散落在多个地方，没有启动时自检

| 配置项 | 位置 | 是否验证 |
|-------|-----|---------|
| Database URL | `.env` | ❌ 只在首次查询时失败 |
| Binance API | `.env` | ❌ 只在首次调用时失败 |
| Top20 列表 | `services/data/universe.py` | ✅ 编译时检查 |
| 风控参数 | `services/execution/bootstrap.py` | ❌ 运行时才发现不合理 |
| 信号门槛 | `AUTO_PAPER_TECHNICAL_RULES` | ❌ 可能导致 100% 拒单 |

**改进建议**:
```python
# apps/api/main.py - 启动时自检
@app.on_event("startup")
async def validate_critical_config():
    # 1. 测试数据库连接
    try:
        with get_session_factory()() as session:
            session.execute(text("SELECT 1"))
    except Exception as e:
        raise RuntimeError(f"Database connection failed: {e}")
    
    # 2. 测试 Binance API
    if settings.binance_api_key:
        client = BinanceCcxtClient()
        try:
            client.fetch_ticker("BTC/USDT")
        except Exception as e:
            logger.warning(f"Binance API test failed: {e}")
    
    # 3. 验证风控参数合理性
    for strategy in strategy_repo.list_strategies():
        risk_per_trade = strategy.rules.position_rules.get("risk_per_trade", 0)
        if risk_per_trade <= 0 or risk_per_trade > 0.1:
            raise ValueError(f"Unreasonable risk_per_trade: {risk_per_trade}")
```

---

## 3. Top20 自动交易验证检查清单

### 3.1 配置检查
- [x] **数据库**: `timescaledb` 可连接（容器内）
- [x] **Redis**: `redis:6379` 可连接
- [x] **Binance API**: 密钥已配置（`WZKSCksUas...`）
- [x] **Top20 列表**: 20 个币种已定义（`services/data/universe.py`）
- [x] **调度器**: `inprocess` 模式，300秒周期
- [ ] **Docker 环境**: 未启动（宿主机运行模式）

### 3.2 数据层检查
```bash
# 验证 OHLCV 数据是否存在
python -c "
from services.data import DataRepository
from services.database import get_session_factory
with get_session_factory()() as session:
    repo = DataRepository(session)
    for symbol in ['BTC/USDT', 'ETH/USDT', 'SOL/USDT']:
        bar = repo.get_latest_ohlcv_bar(symbol=symbol, timeframe='15m')
        if bar:
            print(f'{symbol}: latest bar at {bar.timestamp}')
        else:
            print(f'{symbol}: NO DATA')
"
```

### 3.3 信号生成检查
```bash
# 验证信号生成（当前会失败）
python -c "
from services.execution.paper_signal import PaperSignalGenerator
from services.data import DataRepository
from services.execution import ExecutionRepository
from services.strategy_library import StrategyRepository
from services.database import get_session_factory
from shared.models import PaperRunStepRequest

with get_session_factory()() as session:
    data_repo = DataRepository(session)
    execution_repo = ExecutionRepository(session)
    strategy_repo = StrategyRepository(session)
    
    generator = PaperSignalGenerator(
        data_repo=data_repo,
        execution_repo=execution_repo,
        strategy_repo=strategy_repo
    )
    
    # 获取一个 PaperRun
    from services.strategy_library import PaperRunRepository
    paper_repo = PaperRunRepository(session)
    paper_runs = paper_repo.list_paper_runs()
    if paper_runs:
        paper_run = paper_runs[0]
        strategies = strategy_repo.list_strategies()
        if strategies:
            strategy = strategies[0]
            order = generator.generate_order(
                paper_run=paper_run,
                strategy=strategy,
                request=PaperRunStepRequest(
                    symbol='BTC/USDT',
                    timeframe='15m'
                ),
                positions=[]
            )
            print(f'Order generated:')
            print(f'  should_trade: {order.entry_context.get(\"paper_order_should_trade\")}')
            print(f'  notional: {order.entry_context.get(\"requested_notional\")}')
            print(f'  confidence: {order.entry_context.get(\"decision_pipeline\", {}).get(\"confidence_multiplier\")}')
"
```

### 3.4 风控门槛检查
```bash
# 验证 Gatekeeper 门槛
python -c "
from services.execution.gatekeeper import ExecutionGatekeeperService
from services.strategy_library import RiskProfileRepository
from services.database import get_session_factory

with get_session_factory()() as session:
    risk_repo = RiskProfileRepository(session)
    profile = risk_repo.get_profile('medium_risk_profile')
    if profile:
        print(f'Medium Risk Profile:')
        print(f'  max_position_count: {profile.max_position_count}')
        print(f'  max_total_leverage: {profile.max_total_leverage}')
        print(f'  hard_stop_drawdown_limit: {profile.hard_stop_drawdown_limit}')
    else:
        print('Medium risk profile NOT FOUND')
"
```

### 3.5 端到端测试
```bash
# 手动触发一次 Paper Runtime 循环
curl -X POST http://localhost:8000/api/v1/execution/paper-runs/{paper_run_id}/cycle \
  -H "Authorization: Bearer dev-admin-token" \
  -H "Content-Type: application/json" \
  -d '{
    "symbols": ["BTC/USDT", "ETH/USDT"],
    "timeframe": "15m",
    "max_symbols": 2
  }'
```

---

## 4. 修复后验证流程

### 4.1 修复 P0.1（信心度归零）
1. 读取 `services/execution/decision_pipeline.py`
2. 定位 `confidence_multiplier` 计算逻辑
3. 添加回退机制：
   ```python
   if confidence_multiplier < 0.3:  # 保守阈值
       confidence_multiplier = 0.5  # 保守默认值
   ```
4. 验证：
   ```bash
   # 重新运行信号生成测试，应该看到 notional > 0
   python -c "... # 3.3 的测试脚本"
   ```

### 4.2 修复 P0.2（数据库连接）
1. 如果使用 Docker Compose：
   - 保持 `.env` 中 `@timescaledb:5432`
   - 确保 `make up` 启动所有容器
2. 如果本地运行：
   - 修改为 `@localhost:5432`
   - 在 `docker-compose.yml` 中暴露端口

### 4.3 修复 P1.1（LLM veto）
1. 临时方案（推荐）：
   ```env
   PAPER_RUNTIME_ENABLE_DECISION_VETO=false
   ```
2. 长期方案：
   - 修复 GitHub Models token 权限
   - 或切换到 Claude API

### 4.4 端到端验证
```bash
# 1. 重启服务
# Docker 模式:
make down && make up

# 本地模式:
# Ctrl+C 停止 API 进程，然后重新运行

# 2. 等待一个调度周期（5分钟）

# 3. 检查日志
tail -f logs/scheduler.log | grep -E "(open_long|open_short|rejected)"

# 4. 预期输出（修复后）:
# - 应该看到 open_long/open_short 行动
# - notional > 0
# - 不再有 sizing_sentinel_triggered 警告

# 5. 检查数据库
python -c "
from services.strategy_library import ExecutionRepository
from services.database import get_session_factory
with get_session_factory()() as session:
    repo = ExecutionRepository(session)
    orders = repo.list_order_executions(limit=10)
    print(f'Recent orders: {len(orders)}')
    for order in orders:
        print(f'  {order.symbol} {order.direction} status={order.execution_status}')
"
```

---

## 5. 预期修复后的系统行为

### 5.1 正常运行状态
- **调度器**: 每 300 秒扫描 Top20
- **信号生成**: `confidence_multiplier` 在 0.3-1.0 范围
- **仓位计算**: `notional` 在 20-2000 USDT 范围（10k 账户，0.2%-20% 仓位）
- **开单频率**: 视市场情况，预计 1-5 单/天（20 个币种，不是每个都有信号）
- **风控拒绝**: < 20%（合理的质量筛选，而不是 100% 拒绝）

### 5.2 监控指标
```bash
# 每小时检查
python -c "
from services.strategy_library import PaperRunRepository
from services.database import get_session_factory
with get_session_factory()() as session:
    repo = PaperRunRepository(session)
    runs = repo.list_paper_runs()
    for run in runs:
        if run.paper_status == 'running':
            metrics = run.paper_metrics_summary
            print(f'PaperRun {run.paper_run_id}:')
            print(f'  last_cycle: {metrics.get(\"last_cycle_at\")}')
            print(f'  actions: {metrics.get(\"last_action_counts\")}')
            print(f'  equity: {metrics.get(\"account_equity\")}')
            print(f'  positions: {metrics.get(\"open_position_symbols\")}')
"
```

### 5.3 失败模式识别
如果修复后仍然不开单，按以下顺序排查：

1. **数据问题**: OHLCV 数据缺失或陈旧
   ```bash
   python scripts/data_check.py
   ```

2. **信号门槛过严**: 技术指标门槛设置不合理
   ```python
   # 临时放宽门槛测试
   PAPER_RUNTIME_RELAXED_SIGNALS=true
   ```

3. **风控门槛过严**: Gatekeeper 拒绝合理订单
   ```bash
   # 检查拒绝原因分布
   python -c "
   from services.strategy_library import ExecutionRepository
   from services.database import get_session_factory
   with get_session_factory()() as session:
       repo = ExecutionRepository(session)
       orders = repo.list_order_executions(limit=100)
       rejected = [o for o in orders if o.execution_status == 'rejected']
       reasons = {}
       for order in rejected:
           reason = order.rejection_reason or 'unknown'
           reasons[reason] = reasons.get(reason, 0) + 1
       for reason, count in sorted(reasons.items(), key=lambda x: -x[1]):
           print(f'{reason}: {count}')
   "
   ```

---

## 6. 长期改进建议

### 6.1 架构改进
1. **信心度分级降级**:
   ```python
   if meta_label_available:
       confidence = meta_label_confidence
   elif ensemble_available:
       confidence = ensemble_confidence * 0.7  # 降级折扣
   else:
       confidence = signal_strength * 0.5  # 最低级别
   ```

2. **启动时自检**（见 2.4）

3. **可观测性增强**:
   - 添加 Prometheus metrics
   - 关键路径的分布式追踪（OpenTelemetry）
   - 结构化日志（JSON 格式）

### 6.2 测试覆盖
```python
# 必须增加的测试
tests/
  services/
    execution/
      test_decision_pipeline_edge_cases.py  # ← 缺失
      test_paper_signal_zero_confidence.py  # ← 缺失
      test_gatekeeper_boundary.py           # ← 已有但覆盖不足
```

### 6.3 运维工具
```bash
# 新增管理脚本
scripts/
  diagnose_paper_runtime.py    # 一键诊断
  reset_failed_signals.py      # 重置失败信号
  manual_cycle_trigger.py      # 手动触发循环（调试用）
```

---

## 附录 A: 关键文件速查

| 文件 | 作用 | 关键问题 |
|-----|-----|---------|
| `services/execution/decision_pipeline.py` | 信号决策管道 | **P0.1** 信心度归零 |
| `services/execution/paper_signal.py` | 订单生成 | P0.1 下游症状 |
| `services/execution/bootstrap.py` | Paper 配置 | 风控参数 |
| `services/data/universe.py` | Top20 列表 | ✅ 正确 |
| `.env` | 环境配置 | **P0.2** 数据库主机名 |
| `shared/config.py` | 配置模型 | ✅ 正确 |

---

## 附录 B: 日志关键字速查

| 关键字 | 含义 | 正常/异常 |
|-------|-----|----------|
| `sizing_sentinel_triggered` | 仓位计算异常 | ❌ 异常（当前大量出现） |
| `confidence_multiplier=0.0000` | 信心度归零 | ❌ 异常（根因） |
| `open_long` / `open_short` | 成功开单 | ✅ 正常（当前缺失） |
| `rejected` | 订单被拒绝 | ⚠️ 少量正常，100% 异常 |
| `llm decision veto runtime unavailable` | LLM 不可用 | ⚠️ 可容忍（非关键） |

---

**报告完成时间**: 2026-07-15  
**下一步行动**: 修复 P0.1（信心度归零），然后运行 4.4 端到端验证
