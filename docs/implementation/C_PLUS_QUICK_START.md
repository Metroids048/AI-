# C+ Strategy Quick Start Guide

## 一、验证安装（5分钟）

```bash
# 1. 验证C+流程是否正常工作
python -m scripts.setup_c_plus_strategy --verify-only

# 预期输出：
# [INFO] C+ strategy already exists: c_plus_weighted_ensemble
# [INFO] Verifying C+ pipeline...
# [INFO] Generated X signals for BTC/USDT
# [INFO] C+ decision for BTC/USDT:
#   Decision ID: abc12345
#   Regime: trend_up / range / uncertain
#   Direction: LONG / SHORT / None
#   ...
# [INFO] C+ pipeline verification completed successfully
```

如果出错，检查：
- DataRepository是否能获取OHLCV数据
- 技术指标计算是否正常
- 数据库连接是否正常

## 二、启动Paper模拟（10分钟）

### 方案A：修改现有配置（推荐）

```python
# 编辑 services/execution/bootstrap.py
# 找到这一行：
AUTO_PAPER_TECHNICAL_KEY = "auto_paper_mature_templates"

# 改为：
AUTO_PAPER_TECHNICAL_KEY = "c_plus_weighted_ensemble"
```

### 方案B：创建新Paper Run

```bash
# 使用API或管理界面创建新PaperRun
# strategy_key = "c_plus_weighted_ensemble"
# symbol_scope = ["BTC/USDT", "ETH/USDT"]
```

### 启动调度器

```bash
python -m scripts.run_paper_scheduler
```

## 三、监控运行（实时）

```bash
# 方式1：实时日志
tail -f logs/paper_scheduler.log | grep -i "c_plus\|regime\|weighted"

# 方式2：查看决策漏斗
python -m scripts.audit_decision_funnel \
    --database-url sqlite:///.local_paper_console.db \
    --lookback-days 1

# 方式3：查询数据库
sqlite3 .local_paper_console.db
> SELECT decision_id, symbol, regime, strategy_family, final_side
  FROM trade_decisions
  WHERE strategy_key = 'c_plus_weighted_ensemble'
  ORDER BY timestamp DESC LIMIT 10;
```

## 四、关键指标检查

### 48小时后检查

```sql
-- 1. 候选数和拒绝原因
SELECT
    rejection_reason,
    COUNT(*) as count
FROM trade_decisions
WHERE strategy_key = 'c_plus_weighted_ensemble'
GROUP BY rejection_reason;

-- 2. Regime分布
SELECT
    regime,
    COUNT(*) as count
FROM trade_decisions
WHERE strategy_key = 'c_plus_weighted_ensemble'
GROUP BY regime;

-- 3. 策略族分布
SELECT
    strategy_family,
    final_side,
    COUNT(*) as count
FROM trade_decisions
WHERE strategy_key = 'c_plus_weighted_ensemble'
  AND final_side IS NOT NULL
GROUP BY strategy_family, final_side;

-- 4. 实际成交
SELECT
    symbol,
    side,
    entry_price,
    stop_price,
    take_profit_price,
    status
FROM orders
WHERE strategy_key = 'c_plus_weighted_ensemble'
ORDER BY created_at DESC;
```

### 最小成功标准（72小时）

- ✅ 候选数 > 10
- ✅ 多空候选都有
- ✅ 至少2笔完整开平
- ✅ 不同Regime都出现过
- ✅ 至少2个策略族产生过候选

## 五、故障排查

### 问题：完全没有候选

```bash
# 检查1：信号是否生成
python -c "
from services.data import DataRepository
from services.strategy_library.technical.pandas_ta_adapter import generate_all_technical_signals
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

engine = create_engine('sqlite:///.local_paper_console.db')
with Session(engine) as session:
    data_repo = DataRepository(session)
    signals = generate_all_technical_signals('BTC/USDT', '15m', data_repo)
    print(f'Generated {len(signals)} signals')
    for s in signals[:5]:
        print(f'  {s.source}: {s.side.value} @ {s.confidence}')
"

# 检查2：OHLCV数据是否充足
python -c "
from services.data import DataRepository
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

engine = create_engine('sqlite:///.local_paper_console.db')
with Session(engine) as session:
    data_repo = DataRepository(session)
    bars = data_repo.fetch_ohlcv('BTC/USDT', '15m', limit=200)
    print(f'Fetched {len(bars)} bars')
    if bars:
        print(f'Latest: {bars[-1].timestamp} @ {bars[-1].close}')
"
```

### 问题：候选数很少（<5/天）

**可能原因1：阈值过高**
```python
# 编辑 services/execution/c_plus_config.py
# 降低阈值：
"long_threshold": 0.25,  # 从0.35降到0.25
"short_threshold": -0.25,
```

**可能原因2：市场状态过滤过严**
```python
# 编辑 services/strategy_library/regime/router.py
# 降低ADX阈值：
adx_trend_threshold = 20.0  # 从25.0降到20.0
```

### 问题：只有多单或只有空单

**正常情况**：市场单边趋势时正常
**异常情况**：检查信号源是否失效

```sql
-- 查看原始信号方向分布
SELECT side, COUNT(*)
FROM trade_signals
WHERE timestamp > datetime('now', '-1 day')
GROUP BY side;
```

## 六、调整参数指南

### 提高召回率（产生更多候选）

```python
# c_plus_config.py
"long_threshold": 0.25,           # 降低 (默认0.35)
"short_threshold": -0.25,
"min_total_weight": 0.3,          # 降低 (默认0.5)

# regime/router.py
adx_trend_threshold = 20.0        # 降低 (默认25.0)
```

### 提高精度（减少误信号）

```python
# c_plus_config.py
"long_threshold": 0.45,           # 提高 (默认0.35)
"short_threshold": -0.45,
"min_total_weight": 0.7,          # 提高 (默认0.5)

# entry/models.py
# 各策略族入场条件收紧，例如：
pullback_atr_min = 0.8            # 提高 (默认0.5)
pullback_atr_max = 1.5            # 降低 (默认2.0)
```

### 调整止损距离

```python
# exit/models.py
# TrendPullbackExit
stop_atr_multiple = 2.5           # 加宽 (默认2.0)

# BreakoutExit
stop_atr_multiple = 1.8           # 加宽 (默认1.5)
```

## 七、历史回放（可选，并行执行）

```python
# 创建 scripts/run_c_plus_backtest.py
# TODO: 实现完整的历史回放脚本
# 参考 services/validation/technical_replay.py

# 运行命令（示例）：
python -m scripts.run_c_plus_backtest \
    --symbols BTC/USDT,ETH/USDT,SOL/USDT \
    --start-date 2025-01-01 \
    --end-date 2025-07-01 \
    --timeframe 15m \
    --output artifacts/c_plus_backtest_180d.json
```

## 八、联系和支持

**文档**：
- 实施报告：`docs/implementation/c_plus_strategy_implementation_report.md`
- 交付总结：`docs/implementation/C_PLUS_DELIVERY_SUMMARY.md`

**代码位置**：
- 五层架构：`services/strategy_library/{regime,family,ensemble,entry,exit,c_plus}/`
- 集成适配：`services/execution/c_plus_adapter.py`
- 配置文件：`services/execution/c_plus_config.py`

**常见问题**：
- Q: pytest测试无法运行？
- A: 环境问题，不影响实际运行，可跳过

- Q: 如何切换回旧策略？
- A: 修改`AUTO_PAPER_TECHNICAL_KEY`回`auto_paper_mature_templates`

- Q: 多久能看到结果？
- A: 48-72小时应有初步数据，7-14天有统计意义

---

**开始时间**：现在
**首次检查**：24小时后
**初步评估**：72小时后
**完整评估**：30天后
