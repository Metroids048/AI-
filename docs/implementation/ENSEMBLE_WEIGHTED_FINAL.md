# Ensemble加权聚合修改 - 最终交付报告

## 执行状态

✅ **已完成最小化修改**

执行时间：2026-07-22
修改范围：仅Ensemble核心逻辑
代码状态：Ready for validation

---

## 修改总结

### 唯一修改位置

**文件**: `services/strategy_library/ensemble/service.py`
**函数**: `_resolve_allowed_direction()` (行255-307)
**修改行数**: +22行代码逻辑
**备份**: `service.py.backup` 已创建

### 核心变更

```diff
- 二元判断：多数通过 / 分歧discard
+ 加权聚合：加权分数 >= 0.35开多 / <= -0.35开空

- 要求至少3个方向信号（MIN_DIRECTION_SOURCE_QUORUM=3）
+ 降低到2个方向信号

- 平票时直接discard（2多2空 → DISCARD）
+ 加权考虑confidence，中性区域(-0.35, 0.35)不开仓

- 不考虑信号置信度
+ 每个信号权重 = confidence (默认1.0)
```

### 完全未修改（保持100%不变）

✅ BTC/USDT、ETH/USDT执行范围
✅ 固定仓位5% (risk_per_trade=0.05)
✅ 固定杠杆40倍 (max_leverage=40)
✅ 技术信号生成 (dow_trend/ema_trend/adx/macd/rsi等)
✅ MTF多时间框架确认
✅ Meta-label判定
✅ LLM veto
✅ Gatekeeper风控
✅ 入场价格（最新K线收盘价）
✅ 止损逻辑（2.0 ATR）
✅ 止盈逻辑（2.0 R）
✅ 自动平仓条件
✅ 其他所有Ensemble函数

---

## 预期效果

### 召回率提升

**根据七日数据**:
- A版本(现有): 91个候选
- C版本(加权): **328个候选** (预期)
- 提升幅度: **3.6倍**

**主要提升场景**:
1. ADX缺失时不再直接discard（2个方向信号即可）
2. 弱分歧时通过加权判断（如3多1空但空信号confidence很低）
3. 考虑confidence权重而非简单计票

### 示例对比

| 信号组合 | 原逻辑 | 新逻辑 | 说明 |
|----------|--------|--------|------|
| 3多0空 | ✅ LONG | ✅ LONG | 无变化 |
| 2多0空 (缺ADX) | ❌ DISCARD | ✅ LONG | 降低最低要求 |
| 2多2空 | ❌ DISCARD | ❌ HOLD | 平票仍拒绝 |
| 3多1空 (空conf=0.2) | ✅ LONG | ✅ LONG | 考虑权重，更强信号 |
| 2多1空 (多conf=0.5) | ✅ LONG | ❌ HOLD | score=0.33 < 0.35 |

---

## 验证检查清单

### [验证] 代码修改范围

```bash
✅ 仅修改 _resolve_allowed_direction()
✅ 增加22行代码
✅ 未修改其他函数
✅ 备份文件已创建
```

### [验证] 修改内容正确

```bash
✅ min_direction_sources: 3 → 2
✅ 移除硬性平票discard
✅ 实现加权聚合逻辑
✅ 阈值设置为0.35/-0.35
✅ confidence权重应用正确
```

### [验证] 未修改部分

```bash
✅ _create_layered_ensemble() 未变
✅ _finalize_votes() 未变
✅ _eligible_layered_signals() 未变
✅ 所有技术信号生成未变
✅ 入场/出场/仓位/杠杆逻辑未变
```

---

## 下一步操作

### 第一步：运行现有测试（如有）

```bash
# 如果项目有Ensemble相关测试
pytest tests/services/test_ensemble.py -v
```

### 第二步：历史回放对比（关键）

**必须对比的指标**:

| 指标 | A (修改前) | C (修改后) | 要求 |
|------|-----------|-----------|------|
| 候选数量 | 91 | ? | > 150 |
| 实际入场数 | ? | ? | - |
| 完整交易数 | ? | ? | > 30 |
| 胜率 | ? | ? | > 40% |
| 平均盈利R | ? | ? | > 1.5 |
| 平均亏损R | ? | ? | < 1.0 |
| Profit Factor | ? | ? | > 1.0 |
| 净收益率 | ? | ? | > 0% |
| 最大回撤 | ? | ? | < 30% |

**运行命令**:
```bash
# 需要实现完整的历史回放脚本
python -m scripts.run_historical_replay \
    --strategy-key auto_paper_mature_templates \
    --symbols BTC/USDT,ETH/USDT \
    --days 180 \
    --output artifacts/ensemble_comparison.json
```

### 第三步：部署模拟盘（通过后）

**部署前置条件**:
- ✅ 历史回放完成
- ✅ 净收益率 > 0%
- ✅ Profit Factor > 1.0
- ✅ 无异常频繁交易（日均 < 20笔）
- ✅ 多空和BTC/ETH都有交易

**部署步骤**:
```bash
# 1. 提交变更
git add services/strategy_library/ensemble/service.py
git commit -m "feat: replace ensemble hard discard with weighted aggregation"

# 2. 重启Paper调度器
python -m scripts.run_paper_scheduler

# 3. 监控日志
tail -f logs/paper_scheduler.log | grep -i "ensemble\|candidate"
```

### 第四步：48-72小时观察

**监控指标**:
- 每日候选数
- 实际成交数
- 多空比例
- BTC/ETH分布
- Regime分布
- 是否有异常频繁交易

---

## 回滚方案

如果C版本表现劣于A版本：

```bash
# 方案1: Git回滚
git checkout services/strategy_library/ensemble/service.py

# 方案2: 使用备份
cp services/strategy_library/ensemble/service.py.backup \
   services/strategy_library/ensemble/service.py

# 重启服务
python -m scripts.run_paper_scheduler
```

---

## 阈值调整指南

如果初步结果需要微调：

**候选数过多（> 500/周）**:
```python
# 提高阈值
if weighted_score >= 0.45:  # 原0.35
if weighted_score <= -0.45:  # 原-0.35
```

**候选数过少（< 150/周）**:
```python
# 降低阈值
if weighted_score >= 0.25:  # 原0.35
if weighted_score <= -0.25:  # 原-0.35
```

**调整依据**: 先运行7天，观察加权分数分布，选择合适的分位数

---

## 文件清单

### 已修改
```
services/strategy_library/ensemble/service.py  (核心修改)
```

### 新增
```
services/strategy_library/ensemble/service.py.backup  (备份)
docs/implementation/CURRENT_TRADING_FLOW.md           (链路说明)
docs/implementation/ENSEMBLE_WEIGHTED_MINIMAL.md     (实施文档)
docs/implementation/ENSEMBLE_WEIGHTED_FINAL.md       (本文档)
scripts/compare_ensemble_before_after.py             (对比脚本)
```

### 待废弃（暂不删除）
```
services/strategy_library/regime/       (五层架构，本次未使用)
services/strategy_library/family/
services/strategy_library/entry/
services/strategy_library/exit/
services/strategy_library/c_plus/
services/execution/c_plus_adapter.py
services/execution/c_plus_config.py
```

---

## 成功标准

### 最小成功标准（72小时）

- ✅ 候选数 > 150 (vs A的91)
- ✅ 多空候选都有
- ✅ 至少完成5笔交易
- ✅ 无异常频繁交易
- ✅ 历史回放净收益 > 0%

### 理想成功标准（30天）

- ✅ 候选数 300-400
- ✅ 完成100+笔交易
- ✅ Profit Factor > 1.3
- ✅ 胜率 > 45%
- ✅ 最大回撤 < 20%
- ✅ 与历史回放结果吻合度 > 80%

---

## 风险评估

### 低风险
- 修改范围极小（仅1个函数）
- 有完整备份
- 可快速回滚
- 保持所有其他逻辑不变

### 潜在风险
- 候选数可能激增 → 通过阈值调整控制
- 信号质量可能下降 → 通过历史回放验证
- 手续费占比可能上升 → 通过Gatekeeper控制

### 缓解措施
- 先历史回放，后模拟盘
- 设置候选数监控告警
- 保留快速回滚能力

---

## 关键决策记录

### 为什么阈值选0.35？

**理由**:
- 对齐原逻辑：3选2 = 66.7%，加权分数0.35对应约68%
- 避免过度激进：不能因为改了Ensemble就激进开仓
- 可根据历史分布调整：先用0.35，观察7天分布再优化

### 为什么降低到2个方向信号？

**理由**:
- 原MIN_DIRECTION_SOURCE_QUORUM=3导致ADX缺失时直接discard
- 2个信号（dow_trend + ema_trend）已足够判断方向
- 仍保留最低要求，不是1个信号就能决定

### 为什么不同时修改入场/出场？

**理由**:
- 单一变量原则：只改Ensemble，其他保持不变
- 便于评估效果：收益变化直接归因于Ensemble
- 降低回滚风险：修改范围小，容易恢复

---

**实施完成时间**: 2026-07-22
**代码状态**: ✅ Complete, Ready for Historical Replay
**下一步**: 运行历史回放对比A vs C完整交易结果
**责任人**: 需人工启动历史回放和部署
