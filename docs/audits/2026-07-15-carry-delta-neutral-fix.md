# Carry策略Delta-Neutral修复方案

**日期**: 2026-07-15  
**优先级**: P0（结构性缺陷修复）  
**状态**: 设计中

## 问题描述

当前`_carry_decision`实现只开永续合约单边头寸：
- funding为正时开空永续
- funding为负时开多永续
- **没有同时开现货对冲腿**

这导致：
1. 100%暴露在价格方向风险下（BTC年化波动率60-80%）
2. 资金费率收益（年化12-40%）无法覆盖价格风险
3. 实测净期望值为负（ADR-063）
4. 名不副实：这不是"套利"，是"顺便收点资金费率的裸头寸方向性交易"

## 真正的资金费率套利（Cash-and-Carry）

- **现货多头 + 永续空头**（funding为正时）
- **现货空头 + 永续多头**（funding为负时，需要借币）
- 价格涨跌互相抵消（delta-neutral）
- 唯一收益来源：资金费率本身
- 风险特征：低波动、稳定现金流

## 实施方案

### 方案A：双腿订单模型（推荐）

扩展`ExecutionOrderRequest`支持双腿：

```python
class HedgedLeg(PlatformModel):
    """对冲腿定义"""
    symbol: str  # 现货: "BTC/USDT", 永续: "BTC/USDT:USDT"
    direction: TradeSide
    order_type: OrderType
    is_spot: bool = False  # True=现货, False=永续
    
class ExecutionOrderRequest(PlatformModel):
    # 原有字段...
    hedged_legs: list[HedgedLeg] | None = None  # None=单腿, 非空=多腿对冲
```

**优点**：
- 一次gatekeeper审批，原子性下单
- 风控在组合级别计算
- 清晰表达"这是一个对冲组合"

**缺点**：
- 需要修改执行引擎支持双腿下单
- 需要修改仓位追踪识别对冲组合

### 方案B：策略内部协调两个单腿订单

`_carry_decision`返回时，在`entry_context`中标记"需要对冲腿"，执行引擎识别后自动补充：

```python
entry_context = {
    "requires_hedge_leg": True,
    "hedge_symbol": "BTC/USDT",  # 现货
    "hedge_direction": TradeSide.LONG,
}
```

**优点**：
- 最小改动
- 复用现有单腿下单逻辑

**缺点**：
- 两条腿之间没有原子性保证
- 风控难以在组合级别评估
- 一条腿成功、另一条失败时需要回滚逻辑

### 方案C：独立的Carry订单类型

创建新的`CarryOrderRequest`模型，明确表达双腿：

```python
class CarryOrderRequest(PlatformModel):
    strategy_id: str
    spot_symbol: str  # "BTC/USDT"
    perp_symbol: str  # "BTC/USDT:USDT"
    direction: str  # "short_perp_long_spot" | "long_perp_short_spot"
    notional_usdt: float
    funding_bps: float
    # ...
```

**优点**：
- 类型安全，强制双腿
- 清晰的carry语义

**缺点**：
- 需要独立的执行路径
- 与现有风控、仓位追踪系统集成成本高

## 推荐方案

**短期（本次修复）**：方案B - 策略内部协调  
**中期（层3候选注册表完成后）**：方案A - 双腿订单模型

理由：
1. 方案B改动最小，可以立即修复结构性缺陷
2. 当前carry策略是paper-only，没有testnet/live风险
3. 等层3候选注册表稳定后，再重构为方案A的双腿模型

## 实施步骤

### 第1步：修改`_carry_decision`生成对冲腿信息

```python
def _carry_decision(...) -> DecisionPipelineResult:
    # ... 现有逻辑 ...
    
    # 新增：对冲腿信息
    hedge_leg = None
    if should_trade:
        spot_symbol = symbol  # "BTC/USDT"
        hedge_direction = TradeSide.LONG if direction == TradeSide.SHORT else TradeSide.SHORT
        hedge_leg = {
            "symbol": spot_symbol,
            "direction": str(hedge_direction),
            "order_type": "market",
            "is_spot": True,
            "reason": "delta_neutral_hedge",
        }
    
    return DecisionPipelineResult(
        # ...
        trace={
            # ...
            "hedge_leg": hedge_leg,
        }
    )
```

### 第2步：执行引擎识别并下对冲腿

在`ExecutionEngine`或`PaperExecutor`中：

```python
def execute_order(self, order_request: ExecutionOrderRequest):
    # 下主腿（永续）
    main_leg_result = self._place_order(...)
    
    # 检查是否需要对冲腿
    hedge_leg = order_request.entry_context.get("hedge_leg")
    if hedge_leg and main_leg_result.success:
        hedge_result = self._place_hedge_order(
            symbol=hedge_leg["symbol"],
            direction=hedge_leg["direction"],
            notional=main_leg_result.notional,
            is_spot=hedge_leg["is_spot"],
        )
        if not hedge_result.success:
            # 回滚主腿
            self._cancel_or_close(main_leg_result.order_id)
            return ExecutionResult(success=False, reason="hedge_leg_failed")
    
    return main_leg_result
```

### 第3步：仓位追踪标记对冲组合

```python
class PositionSnapshot(PlatformModel):
    # ... 现有字段 ...
    hedge_group_id: str | None = None  # 对冲组ID
    is_hedge_leg: bool = False  # 是否为对冲腿
```

### 第4步：风控识别对冲组合

在计算`net_directional_exposure`时，对冲组合的两条腿方向相反，净敞口应该接近0。

### 第5步：复盘层报告对冲组合PnL

现货腿和永续腿的PnL应该合并报告为一个carry策略的整体PnL。

## 验证计划

1. **单元测试**：`_carry_decision`返回正确的`hedge_leg`信息
2. **集成测试**：双腿订单在paper模式下正确下单
3. **回测验证**：使用真实数据重新测试carry策略的净期望值
4. **预期结果**：
   - 价格PnL接近0（对冲有效）
   - 净收益 = 资金费率收入 - 双边手续费
   - 净期望值转正

## 风险与限制

### 风险
1. **时间延迟**：现货和永续不是原子下单，存在价格滑移风险
2. **流动性不对称**：现货流动性可能不如永续，大单对冲困难
3. **保证金分离**：现货和永续使用不同保证金账户，需要分别管理
4. **极端行情**：单边强平后另一边裸奔

### 限制
1. **仅支持正资金费率**：当前默认`requires_positive_funding=True`
2. **负资金费率需要借币**：现货做空需要借币机制，暂不支持
3. **Paper-only**：本次修复仅在paper模式验证，不涉及testnet/live

## 后续优化（非本次范围）

1. **跨交易所套利**（层3候选：`cross_exchange_funding_carry`）
2. **借币做空**支持负资金费率套利
3. **基差套利**（合约间价差）
4. **Delta对冲动态再平衡**（gamma scalping）

## 参考文档

- ADR-063：真实数据测试carry策略为负期望值
- 社区资料：BTC 2021-2024资金费率均值0.011%/8h（年化~12%）
- Binance文档：资金费率机制与结算规则
