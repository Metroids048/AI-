# 01 项目诊断报告：自动交易杠杆、仓位预设失效与采样通道越权

版本：1.0  
适用仓库：`https://github.com/Metroids048/AI-`  
参考基线：`main@1855ddc9`  
核对代码来源：用户上传的 `AI--main (2).zip`  
诊断范围：当前真实写单者识别、legacy 仓位/杠杆配置链、采样兜底、价格漂移、反向平仓权限  
明确排除：买卖点策略重写、V2 Active 改造、全仓审计、UI 重构

## 1. 执行摘要

当前症状由多条相互叠加的执行链问题造成，而不是单一参数调优问题：

1. 操作员通过页面/API 保存的仓位与杠杆设置，会写入 `PaperRun.execution_profile`，但 bootstrap 在服务重启时重新生成默认 `execution_profile`，当前合并顺序会覆盖操作员字段。
2. 运行周期优先读取 Active `ConfigSnapshot`；bootstrap 又会将 Manifest 的 `promoted_rules` 重新写入快照。当前仓位计算仍大量读取 `strategy.rules.position_rules`，导致操作员保存的 `order_notional_usdt` 和 `max_leverage` 可能再次被 Manifest 默认值遮蔽。
3. legacy 采样兜底将 `requested_leverage` 强制为 1 倍，并把仓位改成 `max(min_notional, reference_price * 0.0015)`，因此绕过操作员预设。
4. 采样候选已被代码标记为 `NON_PROMOTABLE_PIPELINE_SAMPLE`，但仍可创建订单/仓位，还可能通过 `opposite_signal` 关闭正式策略仓位，权限与证据等级不一致。
5. Binance `set_leverage()` 异常被吞掉，`create_order()` 仍继续执行，本地请求杠杆与交易所实际杠杆可能不一致。
6. 价格漂移门槛从历史的 `20bps / 0.25 ATR` 放宽到 `100bps / 0.40 ATR`，使更多已经偏离信号价的订单得以继续提交。

## 2. 对 DEGRADED 对账行为的纠正

`services/automated_trading/application/entry_service.py` 当前明确写有：

```python
# DEGRADED: per-symbol entry_blocked_symbols handles the affected symbol below;
# unaffected symbols ... are allowed through.
```

当前测试同样验证：

```python
def test_degraded_reconciliation_allows_unaffected_symbol()
```

因此：

- “DEGRADED 只阻塞被隔离币种，不全局阻塞”是当前已有的显式设计。
- 它不是本次仓位/杠杆回归的新增口子。
- 本轮不得修改 `entry_service.py`，也不得把该行为列入当前 P1。
- 是否要改成全局阻塞，应在独立的对账策略评审中处理。

## 3. 当前引擎标志与实际写单者必须分开判断

仓库存在三个模式：

| `AUTOMATED_TRADING_ENGINE` | V2 状态 | legacy writer |
|---|---|---|
| `legacy` | DISABLED | 允许 |
| `v2_shadow` | SHADOW，只写决策事实 | 允许 |
| `v2_active` | ACTIVE | 禁止 |

证据：

- `shared/config.py` 默认值为 `legacy`。
- `scripts/launch-paper-console.ps1` 显式设置 `v2_shadow`。
- `scripts/run-local-paper-scheduler.py` 显式设置 `v2_shadow`。
- `resolve_engine_activation()` 在 `legacy` 和 `v2_shadow` 下都返回 `allow_legacy_writer=True`。

因此标准桌面启动路径通常是：

```text
引擎标志：v2_shadow
实际交易所写单者：legacy
V2：只记录 Shadow 决策，不提交订单
```

只有实际部署为 `v2_active` 时，legacy 修复方案才不适用。

## 4. 已确认问题

### P1-001：bootstrap 覆盖操作员 execution_profile

文件：

- `services/execution/bootstrap.py`

当前逻辑：

```python
profile = {**previous, **execution_profile, **preserved}
```

`execution_profile` 位于 `previous` 后面，因此覆盖已有值。当前 `preserved_keys` 不包含：

- `risk_per_trade`
- `max_leverage`
- `max_symbol_exposure`
- `max_total_exposure`
- `asset_risk_tiers`
- `order_notional_usdt`
- `simulation_sampling_fallback_enabled`
- 其他通过 auto-settings 保存的操作员风险字段

影响：

```text
页面保存
→ 下一周期可能短暂生效
→ 服务重启/bootstrap
→ 默认值重新覆盖
```

### P1-002：运行时仓位计算没有将 operator profile 作为最高优先级

文件：

- `services/execution/paper_signal.py`

当前 `_requested_leverage()`：

```python
if execution_profile has asset_risk_tiers:
    use tier leverage
else:
    strategy.position_rules.max_leverage
    or execution_profile.max_leverage
```

在没有 tier 时，Strategy/Manifest 规则优先于 operator profile。

当前 `_requested_notional()`：

```python
strategy.position_rules.notional_usdt
→ strategy.position_rules.order_notional_usdt
→ strategy.position_rules.risk_per_trade
```

它没有优先读取：

```python
paper_run.execution_profile["order_notional_usdt"]
```

因此即使 API 已经把固定名义金额写入 `execution_profile`，bootstrap 重新阶段化 Manifest rules 后，订单仍可能不用该值。

### P1-003：采样兜底绕过杠杆与仓位预设

文件：

- `services/execution/paper_signal.py`

当前逻辑：

```python
requested_leverage = 1.0 if sampling_mode else ...
```

以及：

```python
requested_notional = max(
    float(min_notional),
    float(reference_price) * 0.0015,
)
```

影响：

- 采样订单永远 1 倍。
- BTC/ETH 仓位会受币价影响，而不是受同一个操作员仓位规则控制。
- `order_notional_usdt`、`risk_per_trade`、资产风险档位均被绕过。

### P1-004：采样候选拥有正式仓位权限

文件：

- `services/execution/paper_signal.py`
- `services/execution/paper_cycle_orchestrator.py`

采样候选被标记为：

```text
decision_variant=simulation_sampling_fallback
testnet_sampling_mode=true
evidence_class=NON_PROMOTABLE_PIPELINE_SAMPLE
strategy_performance_eligible=false
```

但 flat 状态下仍可进入 Gatekeeper、创建订单和仓位；持仓状态下，以下判断未排除采样来源：

```python
request.close_on_opposite_signal
and current_position.side != base_order.direction
```

因此采样 SHORT 可能关闭正式策略 LONG。

### P1-005：杠杆配置失败未阻止新开仓

文件：

- `services/execution/gateway.py`

当前逻辑：

```python
with contextlib.suppress(Exception):
    self.set_leverage(...)
```

异常被吞掉后仍调用 `create_order()`。

现有 `PaperExchangeExecutionService.ensure_binance_execution()` 已经能够捕获 gateway 异常并把订单标记为 Exchange Rejected/Unknown，因此不需要新增一套错误状态机。只需让 gateway 抛出明确错误并禁止继续创建开仓订单。

### P1-006：价格漂移阈值未经证据支持地放宽

文件：

- `shared/config.py`

当前值：

```python
pretrade_min_price_drift_bps = 100.0
pretrade_atr_drift_fraction = 0.40
```

历史值：

```python
20.0
0.25
```

当前没有与该放宽绑定的 OOS 或真实成交证据。应恢复历史值，至少不得高于 `30bps / 0.25 ATR`。

## 5. 不属于本轮的问题

### OBS-001：V2 仓位/杠杆合同

V2 的 `cycle_service.py`、`decision_service.py`、`binance_adapter.py` 存在独立的仓位/杠杆问题，但只有 `v2_active` 是实际写单者时才影响当前订单。

本轮规则：

- `legacy` 或 `v2_shadow`：只修 legacy。
- `v2_active`：冻结 legacy 实施，输出偏差，不得自行改 V2。

### OBS-002：主策略买卖点质量

MACD “最近 6 根内交叉 + 无交叉时退化为持续状态”以及采样信号的单周期状态判断，属于买卖点逻辑专项。

在执行合同修复前，现有成交不能用于评价正式策略。当前任务不修改：

- MACD
- EMA/ADX 规则
- 策略候选
- 止盈止损模型
- 入场触发框架

## 6. 根因模型

```text
操作员设置
→ 写入 PaperRun.execution_profile 和 NEXT_CYCLE ConfigSnapshot
→ bootstrap 重启覆盖 PaperRun profile
→ Manifest sync 重建 strategy_rules
→ runtime sizing 优先读取 strategy_rules
→ operator order_notional/max_leverage 失去优先级
```

同时：

```text
正式策略无信号
→ simulation_sampling_fallback
→ 1x
→ price × 0.0015 仓位
→ market order
→ 宽松 1% 追价
→ 可创建仓位
→ 可反向平正式仓位
```

## 7. 修复完成的必要条件

以下条件缺一不可：

1. 识别实际写单者。
2. 采样候选不再创建正式仓位，也不再关闭正式仓位。
3. 新开仓的杠杆配置失败时，`create_order()` 不得调用。
4. 价格漂移恢复历史严格值。
5. operator profile 在 bootstrap 后保持不变。
6. runtime sizing 将 operator profile 作为最高配置真源。
7. 保存设置 → 激活快照 → bootstrap → 下一周期的合同测试通过。
8. 不修改 DEGRADED 对账语义，不修改 V2，不修改买卖点策略。
