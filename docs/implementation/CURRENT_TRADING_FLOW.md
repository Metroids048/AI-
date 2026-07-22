# 当前真实交易链路说明

## 执行范围配置

### BTC/ETH配置位置
**文件**: `services/execution/bootstrap.py`
**行号**: 25
```python
AUTO_PAPER_EXECUTION_SYMBOLS = ("BTC/USDT", "ETH/USDT")
```

### 固定仓位配置
**文件**: `services/execution/bootstrap.py`
**行号**: 114
```python
"risk_per_trade": PAPER_RUNTIME_LIMITS["risk_per_trade"],  # 实际值：0.05 (5%)
```

**定义位置**: `shared/models/risk.py`
```python
PAPER_RUNTIME_LIMITS = {
    "risk_per_trade": 0.05,  # 单笔风险5%
    ...
}
```

### 固定杠杆配置
**文件**: `services/execution/bootstrap.py`
**行号**: 116
```python
"max_leverage": PAPER_RUNTIME_LIMITS["max_leverage"],  # 实际值：40倍
```

**定义位置**: `shared/models/risk.py`
```python
PAPER_RUNTIME_LIMITS = {
    "max_leverage": 40,
    ...
}
```

## 策略调度频率

**调度入口**: `services/execution/scheduler.py` 中的 `PaperScheduler`
**调度频率**: 每个时间框架的K线收盘后触发
**主要时间框架**:
- `direction_timeframe`: 4h
- `entry_timeframe`: 15m

## 基础信号生成

### 方向信号（DIRECTION_SOURCES）

**文件**: `services/strategy_library/ensemble/service.py`
**行号**: 24-26
```python
DIRECTION_SOURCES = frozenset(
    {"technical_dow_trend", "technical_ema_trend", "technical_adx", "technical_mtf_ma"}
)
```

**配置**: `services/execution/bootstrap.py` 行69
```python
"direction_signals": ["dow_trend", "ema_trend", "adx", "mtf_ma"]
```

**生成位置**: `services/execution/decision_pipeline.py` 行151
```python
signals = self._technical_signals(frame=frame, symbol=symbol, enabled_signals=enabled_signals)
```

**具体函数**:
- `generate_dow_trend_signal()` - `services/strategy_library/technical/dow_trend.py`
- `generate_ema_trend_signal()` - `services/strategy_library/technical/indicators.py`
- `generate_adx_trend_signal()` - `services/strategy_library/technical/indicators.py`
- `generate_multi_timeframe_ma_signal()` - `services/strategy_library/technical/indicators.py`

### 入场信号（ENTRY_SOURCES）

**文件**: `services/strategy_library/ensemble/service.py`
**行号**: 27
```python
ENTRY_SOURCES = frozenset({"technical_macd", "technical_rsi", "technical_fvg", "market_intelligence"})
```

**配置**: `services/execution/bootstrap.py` 行70
```python
"entry_signals": ["macd", "price_action", "rsi", "vwap", "bollinger", "fvg"]
```

**具体函数**:
- `generate_macd_signal()` - `services/strategy_library/technical/macd.py`
- `generate_rsi_signal()` - `services/strategy_library/technical/indicators.py`
- `generate_fvg_signal()` - `services/strategy_library/technical/price_action.py`
- `generate_price_action_signals()` - `services/strategy_library/technical/price_action.py`
- `generate_vwap_reclaim_signal()` - `services/strategy_library/technical/indicators.py`
- `generate_bollinger_reversion_signal()` - `services/strategy_library/technical/indicators.py`

### 区间信号（RANGE_SOURCES）

**文件**: `services/strategy_library/ensemble/service.py`
**行号**: 28
```python
RANGE_SOURCES = frozenset({"technical_vwap", "technical_bollinger"})
```

## 当前Ensemble判定逻辑（需要修改的部分）

### 核心函数

**文件**: `services/strategy_library/ensemble/service.py`
**函数**: `_create_layered_ensemble()` 行85-122
**调用位置**: `decision_pipeline.py` 行216

### 当前通过条件

**步骤1: 解析方向 `_resolve_allowed_direction()`** (行255-284)

**当前逻辑**:
```python
# 行278: 至少MIN_DIRECTION_SOURCE_QUORUM个方向信号才能判定
if len(by_source) < min_direction_sources:
    return None  # DISCARD：方向信号不足

# 行280-281: 统计多空票数
long_votes = sum(1 for direction in by_source.values() if direction == TradeSide.LONG)
short_votes = sum(1 for direction in by_source.values() if direction == TradeSide.SHORT)

# 行282-284: 必须有明确多数，不能平票
if long_votes == short_votes:
    return None  # DISCARD：方向分歧
return TradeSide.LONG if long_votes > short_votes else TradeSide.SHORT
```

**步骤2: 筛选入场信号 `_eligible_layered_signals()`** (行286-304)

**当前逻辑**:
```python
# 只保留与allowed_direction同向的entry信号
if layer == "entry" and signal.direction == allowed_direction:
    eligible.append(signal)
```

**步骤3: 最终投票 `_finalize_votes()`** (行124-161)

**当前逻辑**:
```python
# 行142-144: 计算多空加权分数
long_score = sum(v.weight * (v.confidence or 1.0) for v in votes if v.direction == TradeSide.LONG)
short_score = sum(v.weight * (v.confidence or 1.0) for v in votes if v.direction == TradeSide.SHORT)

# 行145-150: 选择分数更高的方向
if long_score >= short_score:
    direction = TradeSide.LONG
    confidence = long_score / total_score if total_score else 0.0
```

### 当前discard条件汇总

**位置1: 方向信号不足**
`ensemble/service.py:278` - 少于MIN_DIRECTION_SOURCE_QUORUM(3)个方向信号

**位置2: 方向平票**
`ensemble/service.py:282` - 多空票数相等

**位置3: 无有效票**
`ensemble/service.py:132` - 最终votes为空，返回`DISCARDED_LOW_CONFIDENCE`

## Multi-Timeframe确认

**位置**: `decision_pipeline.py` 行191-197
```python
multi_timeframe = self._multi_timeframe_confirmation(...)
if not multi_timeframe["passed"] and not relaxed_signals:
    return self._skipped(reason="multi_timeframe_disagreement", ...)
```

**MTF逻辑**: 检查4h和15m时间框架是否一致

## LLM介入点

**位置**: `decision_pipeline.py` 行250-304

**函数**: `_decision_veto()` 行714-841

**触发条件**:
```python
if enable_decision_veto and settings.decision_veto_enabled:
    veto_result = self._decision_veto(...)
```

**LLM判定**:
- 调用LLM分析信号和市场情况
- 返回`should_block=True`时拒绝候选

## Gatekeeper阻断点

**文件**: `services/execution/gatekeeper.py`

**函数**: `validate_execution_order()` 行78-159

**阻断规则**:
1. Kill switch激活
2. 风险状态超限
3. 仓位过大
4. 杠杆超限
5. API失败次数过多
6. Martingale检测

## 入场价格确定

**文件**: `services/execution/paper_signal.py`

**函数**: `_decision_for_strategy()` 行198-275

**逻辑**:
```python
# 行250-258: 从decision_pipeline获取reference_price
decision = self._decision_for_strategy(...)
reference_price = decision.reference_price

# 行260-266: reference_price来自最新K线收盘价
# decision_pipeline.py 行144
reference_price = Decimal("0") if latest is None else latest.close
```

## 止损止盈确定

**文件**: `services/execution/paper_signal.py`

**函数**: `_risk_prices()` 行277-315

**止损逻辑**:
```python
# 行288-295: 基于ATR倍数
atr_multiple = strategy.rules.stoploss_rules.get("atr_multiple", 2.0)
if atr and atr > 0:
    stop_distance = Decimal(str(atr)) * Decimal(str(atr_multiple))
```

**止盈逻辑**:
```python
# 行297-302: 基于风险回报比
risk_reward = strategy.rules.takeprofit_rules.get("risk_reward", 2.0)
takeprofit_price = entry_price + stop_distance * Decimal(str(risk_reward))  # LONG
takeprofit_price = entry_price - stop_distance * Decimal(str(risk_reward))  # SHORT
```

**当前配置值**:
- `atr_multiple`: 2.0 (bootstrap.py 行104)
- `risk_reward`: 2.0 (bootstrap.py 行112)

## 自动平仓原因

**文件**: `services/execution/paper_cycle_orchestrator.py`

**触发条件**:
1. 止损触发
2. 止盈触发
3. 反向信号 (`close_on_opposite_signal: True`)
4. 时间止损 (`time_exit_hours: 24`)

## 完整订单流程

```
1. Scheduler定时触发 (scheduler.py)
   ↓
2. 生成技术信号 (decision_pipeline.py:151 _technical_signals)
   ↓
3. MTF确认 (decision_pipeline.py:191 _multi_timeframe_confirmation)
   ↓
4. Ensemble判定 (ensemble/service.py:85 _create_layered_ensemble)
   ↓ [关键：这里会discard]
5. Meta-label判定 (decision_pipeline.py:225)
   ↓
6. LLM veto (decision_pipeline.py:250 _decision_veto)
   ↓
7. 生成订单请求 (paper_signal.py:77 generate_order)
   ↓
8. Gatekeeper验证 (gatekeeper.py:78 validate_execution_order)
   ↓
9. 提交到交易所 (paper_exchange_execution_service.py)
   ↓
10. 监控平仓条件 (paper_cycle_orchestrator.py)
```

## 需要修改的唯一位置

**文件**: `services/strategy_library/ensemble/service.py`
**函数**: `_create_layered_ensemble()` 和 `_resolve_allowed_direction()`
**行号**: 85-122 和 255-284

**修改目标**:
- 将二元判断（通过/discard）改为加权聚合
- 保留所有信号，按现有含义规范化
- 计算综合方向分，达到阈值才形成候选
- 不改变入场价格、止损止盈、仓位、杠杆的任何逻辑

---

**生成时间**: 2026-07-22
**当前策略**: auto_paper_mature_templates
**当前Ensemble方法**: layered_regime_entry
**MIN_DIRECTION_SOURCE_QUORUM**: 3 (可放宽到2)
