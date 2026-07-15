# Carry策略Delta-Neutral修复 - 完成报告

**日期**: 2026-07-15  
**状态**: ✅ 核心实现已完成  
**优先级**: P0（结构性缺陷修复）

## 已完成的工作

### ✅ 第1步：信号生成层 - hedge_leg信息

**文件**: `services/execution/paper_signal.py`

- 修改`_carry_decision`方法（第322-334行）
- 当`should_trade=True`时生成现货对冲腿信息
- 对冲腿方向与永续相反（永续SHORT→现货LONG）
- 信息存储在`trace["hedge_leg"]`中

### ✅ 第2步：执行引擎 - 双腿订单执行

**文件**: `services/execution/paper_runtime.py`

**核心逻辑**（第934-1050行）：
1. 提交并成交主腿（永续合约）订单
2. 检测`decision_trace.get("hedge_leg")`
3. 如果存在对冲腿信息：
   - 创建对冲腿订单请求（`_create_hedge_order_request`）
   - 提交对冲腿订单到gatekeeper
   - 成交对冲腿订单
   - 如果对冲腿失败→拒绝主腿（防止裸头寸）
4. 创建主腿仓位
5. 创建对冲腿仓位
6. 标记两条腿为同一个hedge_group

**新增方法**：
- `_create_hedge_order_request`: 创建对冲腿订单请求
  - 使用与主腿相同的notional实现真正的delta-neutral
  - 复制止损/止盈逻辑到对冲腿
  - 标记`is_hedge_leg=True`
- `_mark_position_as_hedged`: 标记仓位为对冲组成员
  - 设置`hedge_group_id`
  - 设置`is_hedge_leg`标志

### ✅ 第3步：数据模型 - 对冲组字段

**文件**: `shared/models/workflow.py`

**PositionSnapshot模型新增字段**：
```python
hedge_group_id: str | None = None  # 对冲组ID
is_hedge_leg: bool = False  # 是否为对冲腿
```

**文件**: `services/strategy_library/models.py`

**数据库模型新增字段**：
```python
hedge_group_id: Mapped[str | None] = mapped_column(String(60), nullable=True, index=True)
is_hedge_leg: Mapped[bool] = mapped_column(default=False)
```

### ✅ 第4步：风控层 - 识别对冲组合

**文件**: `services/execution/gatekeeper.py`

**修改`_evaluate_numeric_risk`方法**：
- 检测`entry_context.get("is_hedge_leg")`
- 对冲腿跳过`net_directional_exposure`检查
- 原理：对冲腿与主腿方向相反，净敞口互相抵消

### ✅ 测试文件

**文件**: `tests/test_carry_delta_neutral_fix.py`

包含3个测试用例：
1. 正常情况生成hedge_leg
2. 拒绝订单时不生成hedge_leg
3. 负资金费率时对冲方向正确

## 工作原理示例

### 场景：BTC资金费率为正（0.01%）

**信号生成**（paper_signal.py）：
```
永续方向: SHORT (做空永续收取资金费率)
对冲腿: {
  symbol: "BTC/USDT" (现货)
  direction: "LONG" (做多现货对冲价格风险)
  is_spot: True
}
```

**执行流程**（paper_runtime.py）：
```
1. 下永续SHORT订单（如$10,000 notional）
2. 下现货LONG订单（同样$10,000 notional）
3. 如果现货订单失败 → 拒绝永续订单（防止裸头寸）
4. 两条腿都成功 → 创建仓位并标记为hedge_group
```

**风险评估**（gatekeeper.py）：
```
永续SHORT: net_directional_exposure -= 10,000 / account_equity
现货LONG: is_hedge_leg=True, 跳过净敞口检查
实际净敞口: ≈ 0 (delta-neutral)
```

**PnL分解**（预期）：
```
价格涨跌PnL: 永续亏损 ≈ 现货盈利 (对冲有效)
资金费率收入: 每8小时收取 (如0.01% × $10,000 = $1)
双边手续费: 4笔成交 × (fee + slippage)
净收益: 资金费率 - 手续费
```

## 关键设计决策

### 1. 为什么在runtime层实现而非使用CarryExecutionService？

**原因**：
- `CarryExecutionService`设计用于testnet/live真实交易
- Paper模式使用不同的执行流程（模拟成交）
- 在runtime层实现避免引入gateway抽象的复杂性

**未来优化**：
- 当paper模式支持gateway抽象后，可重构为使用`CarryExecutionService`

### 2. 对冲腿失败的处理策略

**当前实现**：对冲腿失败→拒绝主腿

**备选方案**：
- 主腿已成交→立即平仓回滚（需要实现补偿逻辑）
- 允许部分对冲失败（风险较高）

**选择理由**：
- Paper模式下，拒绝订单成本低
- 避免复杂的回滚逻辑
- 确保绝不会出现裸头寸

### 3. 风控层对对冲腿的特殊处理

**实现**：跳过`net_directional_exposure`检查

**原理**：
- 对冲腿与主腿方向相反
- 净敞口应该互相抵消
- 如果检查对冲腿，会重复计算风险

## 待完成工作（非本次范围）

### ⏸️ 数据库迁移

**需要**：创建Alembic迁移脚本添加`hedge_group_id`和`is_hedge_leg`字段

**命令**：
```bash
alembic revision -m "add_hedge_group_fields_to_position_snapshots"
alembic upgrade head
```

### ⏸️ 第5步：复盘层报告对冲组合PnL

**需要修改的文件**：
- `services/strategy_library/review.py` (如果存在)
- 识别`hedge_group_id`相同的仓位
- 合并报告为一个carry策略的PnL
- 分离显示：价格PnL、资金费率收入、手续费、净收益

### ⏸️ 平仓逻辑

**当前问题**：
- 只实现了开仓的双腿逻辑
- 平仓时需要同时平掉现货和永续

**需要修改**：
- `paper_runtime.py`的平仓流程
- 检测`hedge_group_id`
- 同时平仓对冲组的所有腿

### ⏸️ 真实数据验证

**需要**：
- 使用历史BTC数据重新回测carry策略
- 验证价格PnL≈0（对冲有效性）
- 验证净期望值是否转正

## 验证计划

### 单元测试

```bash
# 使用全局Python环境的pytest
pytest tests/test_carry_delta_neutral_fix.py -v
```

### 集成测试

1. 启动paper runtime
2. 配置carry策略：`AUTO_PAPER_RUNTIME_KEY = "auto_paper_btc_funding"`
3. 观察日志确认双腿订单正确执行
4. 检查数据库`position_snapshots`表：
   - 同一个`hedge_group_id`的两条记录
   - 一条`is_hedge_leg=False`（永续）
   - 一条`is_hedge_leg=True`（现货）

### 回测验证

1. 获取BTC历史资金费率数据（2021-2024）
2. 模拟delta-neutral carry策略
3. 计算净收益 = 资金费率收入 - 双边手续费
4. 验证价格PnL≈0
5. 确认净期望值 > 0

## 文件清单

### 修改的文件

1. ✅ `services/execution/paper_signal.py` - 生成hedge_leg信息
2. ✅ `services/execution/paper_runtime.py` - 双腿执行逻辑
3. ✅ `shared/models/workflow.py` - PositionSnapshot模型
4. ✅ `services/strategy_library/models.py` - 数据库模型
5. ✅ `services/execution/gatekeeper.py` - 风控层

### 新增的文件

1. ✅ `docs/audits/2026-07-15-carry-delta-neutral-fix.md` - 设计文档
2. ✅ `tests/test_carry_delta_neutral_fix.py` - 单元测试
3. ✅ `docs/audits/2026-07-15-carry-delta-neutral-implementation-status.md` - 实施状态
4. ✅ 本文件 - 完成报告

## 风险与限制

### 已知风险

1. **时间延迟风险**：现货和永续不是原子下单，存在价格滑移
2. **流动性不对称**：现货流动性可能不如永续
3. **保证金分离**：现货和永续使用不同保证金账户
4. **极端行情**：单边强平后另一边裸奔

### 当前限制

1. **仅支持正资金费率**：`requires_positive_funding=True`
2. **负资金费率需要借币**：现货做空需要借币机制（未实现）
3. **Paper-only**：本次修复仅在paper模式，不涉及testnet/live
4. **平仓未完成**：只实现开仓双腿，平仓还需要补充

## 性能影响

### 额外开销

- 每个carry订单：2次gatekeeper调用（主腿+对冲腿）
- 每个carry订单：2条position_snapshot记录
- 风控计算：额外的`is_hedge_leg`检查

### 预期影响

- **可忽略**：对冲腿检查是O(1)操作
- **存储增加**：每个carry仓位2倍记录（可接受）

## 下一步建议

### 立即执行

1. **创建数据库迁移**：添加hedge_group_id字段
2. **补充平仓逻辑**：同时平掉对冲组的所有腿
3. **运行集成测试**：验证双腿执行流程

### 短期优化

1. **实施复盘层**：合并报告对冲组合PnL
2. **真实数据回测**：验证净期望值转正
3. **补充文档**：更新AGENTS.md和README

### 中长期重构

1. **统一使用CarryExecutionService**：paper/testnet/live共用
2. **支持负资金费率**：实现借币做空现货
3. **跨交易所套利**：扩展到cross_exchange_funding_carry

## 结论

本次修复实现了carry策略从"裸头寸"到"真正delta-neutral套利"的结构性升级：

- ✅ **问题根源已修复**：不再是裸持永续顺便收点资金费率
- ✅ **架构清晰可扩展**：hedge_group机制可复用到其他对冲策略
- ✅ **风控正确识别**：对冲组合不会被误判为过度敞口
- ⏸️ **验证待完成**：需要真实数据回测确认净期望值转正

**核心成果**：项目现在有了一个符合"套利"定义的真正delta-neutral carry策略框架。
