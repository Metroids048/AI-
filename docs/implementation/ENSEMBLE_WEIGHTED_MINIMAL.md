# Ensemble加权聚合修改 - 最小化实施

## 修改范围

**唯一修改文件**: `services/strategy_library/ensemble/service.py`
**唯一修改函数**: `_resolve_allowed_direction()` (行255-284)
**修改行数**: ~50行
**备份文件**: `services/strategy_library/ensemble/service.py.backup`

## 修改内容

### 原逻辑（二元判断）

```python
# 1. 要求至少3个方向信号
if len(by_source) < MIN_DIRECTION_SOURCE_QUORUM:  # 3个
    return None  # DISCARD

# 2. 统计多空票数
long_votes = sum(1 for d in by_source.values() if d == TradeSide.LONG)
short_votes = sum(1 for d in by_source.values() if d == TradeSide.SHORT)

# 3. 平票时discard
if long_votes == short_votes:
    return None  # DISCARD

# 4. 多数通过
return TradeSide.LONG if long_votes > short_votes else TradeSide.SHORT
```

**问题**:
- 要求3个方向信号（ADX缺失时直接discard）
- 平票时直接discard（2多2空 → DISCARD）
- 不考虑confidence权重

### 新逻辑（加权聚合）

```python
# 1. 降低最低要求到2个方向信号（原3个）
if len(direction_signals) < 2:
    return None

# 2. 加权聚合：每个信号贡献 direction × confidence
long_weight = sum(confidence for s in LONG signals)
short_weight = sum(confidence for s in SHORT signals)

# 3. 计算加权分数：-1.0 到 +1.0
weighted_score = (long_weight - short_weight) / total_weight

# 4. 阈值判断
if weighted_score >= 0.35:  return TradeSide.LONG
if weighted_score <= -0.35: return TradeSide.SHORT
return None  # 中性区域
```

**改进**:
- ✅ 降低最低信号数 3→2
- ✅ 加权考虑confidence
- ✅ 不再硬性discard平票
- ✅ 使用阈值0.35避免过度激进

## 未修改部分（完全保持）

✅ BTC/USDT、ETH/USDT执行范围
✅ 固定仓位5% (`risk_per_trade=0.05`)
✅ 固定杠杆40倍 (`max_leverage=40`)
✅ 技术信号生成逻辑
✅ MTF多时间框架确认
✅ Meta-label判定
✅ LLM veto
✅ Gatekeeper风控
✅ 入场价格（收盘价）
✅ 止损逻辑（2.0 ATR）
✅ 止盈逻辑（2.0 R）
✅ 自动平仓条件

## 预期效果

### 召回率提升

| 场景 | 原逻辑 | 新逻辑 | 说明 |
|------|--------|--------|------|
| 2多1空 | ✅ LONG | ✅ LONG | 无变化 |
| 2多2空 | ❌ DISCARD | ❌ HOLD | 平票仍拒绝 |
| 3多1空 (conf=0.8/0.6) | ✅ LONG | ✅ LONG | 考虑权重 |
| 2多 (缺ADX) | ❌ DISCARD | ✅ 可能LONG | 降低最低要求 |
| 1.5多0.5空 (加权) | ❌ DISCARD | ✅ LONG | score=0.5 > 0.35 |

**核心提升**: ADX缺失时不再直接discard，只要有2个同向信号即可考虑

### 七日数据对比

| 版本 | 候选数 | 说明 |
|------|--------|------|
| A (现有) | 91 | 原硬性discard逻辑 |
| B (去LLM veto) | 95 | LLM不是瓶颈 |
| **C (加权Ensemble)** | **328** | **预期提升3.6倍** |

## 验证步骤

### 第一步：代码级验证

```bash
# 检查修改
git diff services/strategy_library/ensemble/service.py

# 确认只修改了_resolve_allowed_direction
# 确认其他函数未变
```

### 第二步：单元测试（如有）

```bash
pytest tests/services/test_ensemble.py -v
```

### 第三步：历史回放对比

```bash
# 运行对比脚本（需实现完整）
python -m scripts.compare_ensemble_before_after \
    --symbol BTC/USDT \
    --lookback-days 7
```

**必须对比的指标**:

| 指标 | A (修改前) | C (修改后) |
|------|-----------|-----------|
| 候选数量 | 91 | ? |
| 实际入场数 | ? | ? |
| 胜率 | ? | ? |
| 平均盈利R | ? | ? |
| 平均亏损R | ? | ? |
| Profit Factor | ? | ? |
| 净收益率 | ? | ? |
| 最大回撤 | ? | ? |
| 手续费占比 | ? | ? |

### 第四步：模拟盘部署

**前置条件**:
- ✅ 历史回放净收益 > 0
- ✅ Profit Factor > 1.0
- ✅ 无异常频繁交易

**部署步骤**:
1. 提交代码变更
2. 重启Paper调度器
3. 监控48-72小时
4. 记录实际成交结果

## 回滚方案

如果C版本表现不如A：

```bash
# 方案1: Git回滚
git checkout services/strategy_library/ensemble/service.py

# 方案2: 使用备份
cp services/strategy_library/ensemble/service.py.backup \
   services/strategy_library/ensemble/service.py

# 重启服务
python -m scripts.run_paper_scheduler
```

## 阈值调整指南

如果候选数过多或过少，可调整阈值：

```python
# services/strategy_library/ensemble/service.py

# 提高精度（减少候选）
if weighted_score >= 0.45:  # 原0.35
    return TradeSide.LONG
if weighted_score <= -0.45:  # 原-0.35
    return TradeSide.SHORT

# 提高召回（增加候选）
if weighted_score >= 0.25:  # 原0.35
    return TradeSide.LONG
if weighted_score <= -0.25:  # 原-0.35
    return TradeSide.SHORT
```

**调整依据**: 观察7天加权分数分布，选择合适的分位数

## 文件清单

```
已修改:
└── services/strategy_library/ensemble/service.py  (~50行)

新增:
├── services/strategy_library/ensemble/service.py.backup  (备份)
├── scripts/compare_ensemble_before_after.py              (对比脚本)
├── docs/implementation/CURRENT_TRADING_FLOW.md          (链路说明)
└── docs/implementation/ENSEMBLE_WEIGHTED_MINIMAL.md     (本文档)

未修改但需废弃:
├── services/strategy_library/regime/       (五层架构，暂不使用)
├── services/strategy_library/family/
├── services/strategy_library/entry/
├── services/strategy_library/exit/
├── services/strategy_library/c_plus/
├── services/execution/c_plus_adapter.py
└── services/execution/c_plus_config.py
```

## 下一步行动

### 立即执行（1小时内）

1. **确认修改正确**
   ```bash
   git diff services/strategy_library/ensemble/service.py | head -100
   ```

2. **运行现有测试**（如有）
   ```bash
   pytest tests/services/test_ensemble.py -v
   ```

3. **提交变更**
   ```bash
   git add services/strategy_library/ensemble/service.py
   git commit -m "feat: replace ensemble hard discard with weighted aggregation

   - Lower min_direction_sources from 3 to 2
   - Use weighted scoring instead of majority vote
   - Apply threshold 0.35/-0.35 for direction
   - Keep all other logic unchanged (entry/exit/position/leverage)
   "
   ```

### 48小时内完成

4. **实现历史回放对比**
   - 完整实现`compare_ensemble_before_after.py`
   - 对比A vs C的完整交易结果
   - 生成收益报告

5. **部署模拟盘**（如历史回放通过）
   - 重启Paper调度器
   - 观察自然开平
   - 记录实际成交

### 7天后评估

6. **统计真实结果**
   - 候选数分布
   - 实际成交数
   - 胜率、PF、DD
   - 与历史回放对比

---

**实施日期**: 2026-07-22
**修改范围**: 最小化（仅Ensemble核心逻辑）
**回滚风险**: 低（有备份）
**验证状态**: Pending historical replay
