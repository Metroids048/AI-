# 02 冻结解决方案 v1.0

版本：1.0  
状态：FROZEN  
目标：修复 legacy 实际写单链中的杠杆/仓位预设失效、采样越权、杠杆失败继续下单和价格追入放宽  
适用条件：实际写单者为 legacy；`AUTOMATED_TRADING_ENGINE=legacy` 或 `v2_shadow`  
不适用条件：实际写单者为 V2 Active

## 1. 权威结论

### 1.1 DEGRADED 对账不属于本轮

`DEGRADED` 采用 per-symbol quarantine 是已有显式设计，不是本次新增回归。

冻结要求：

- 不修改 `services/automated_trading/application/entry_service.py`。
- 不修改相关测试断言。
- 不将其作为当前修复的理由。

### 1.2 “引擎标志”和“写单者”是两个字段

阶段 0 必须输出：

```text
AUTOMATED_TRADING_ENGINE=<value>
V2 activation=<DISABLED|SHADOW|ACTIVE>
allow_legacy_writer=<true|false>
actual recent order writer=<legacy|v2|unknown>
```

`v2_shadow` 仍由 legacy 写单，不得误判成 V2 写单。

## 2. 问题与方案映射

| 问题 | 方案 |
|---|---|
| P1-001 bootstrap 覆盖 operator profile | S-201 |
| P1-002 runtime sizing 不以 operator profile 为最高优先级 | S-202 |
| P1-003 采样 1x 与 price×0.0015 | S-101、S-104 |
| P1-004 采样可开仓/反向平仓 | S-101、S-105 |
| P1-005 set_leverage 失败仍 create_order | S-102 |
| P1-006 漂移放宽到 100bps/0.40ATR | S-103 |

## 3. 实施阶段

### 阶段 0：写单者确认

#### S-000：确认实际写单路径

允许读取：

- 实际 `.env`
- PowerShell 启动脚本
- Compose/K8s/Systemd 配置
- 运行进程环境
- 运行数据库最近三天订单

禁止修改代码。

必须执行：

```bash
git branch --show-current
git rev-parse HEAD
git status --short
```

配置核实：

```bash
python - <<'PY'
from shared.config import settings
from services.automated_trading.infrastructure.runtime_lock import resolve_engine_activation

resolved = resolve_engine_activation(settings)
print("engine_flag=", settings.automated_trading_engine)
print("v2_activation=", resolved.v2_activation.value)
print("allow_legacy_writer=", resolved.allow_legacy_writer)
print("execution_mode=", resolved.execution_mode.value)
PY
```

同时检查真实启动方式是否覆盖变量：

```bash
rg -n "AUTOMATED_TRADING_ENGINE" .env* scripts infra docker-compose*.yml
```

如果有运行数据库，抽样最近开仓：

- `decision_variant`
- `testnet_sampling_mode`
- `candidate_id`
- `requested_leverage`
- `requested_notional`
- `order_type`
- `config_snapshot_id`
- `config_hash`

决策规则：

- `legacy`：继续实施。
- `v2_shadow` 且 `allow_legacy_writer=True`：继续实施，报告写“标志为 V2 Shadow，实际 writer 为 legacy”。
- `v2_active` 或最近订单明确来自 V2：停止 S-101～S-203，输出 `DEVIATION-ENGINE-001`。
- 无法访问部署环境：按仓库标准桌面路径 `v2_shadow + legacy writer` 实施，但明确标记假设。

---

## 4. 阶段 1：立即止血

### S-101：采样兜底默认关闭且关闭值可跨重启保留

对应问题：P1-003、P1-004

修改：

- `services/execution/bootstrap.py`
- `tests/services/test_paper_bootstrap.py`

冻结行为：

1. 新创建的 directional PaperRun 默认：

```python
"simulation_sampling_fallback_enabled": False
```

2. 已有 run 的该字段只要存在，就必须跨 bootstrap 保留，包括显式 `False`。
3. 部署时必须通过现有 execution-profile API 或数据库操作，将当前运行 run 的值明确设置为 `False`。
4. 不删除 sampling decision 代码；只关闭默认入口并确保 operator kill switch 稳定。

最小实现：

- 将 `simulation_sampling_fallback_enabled` 加入始终保留的键。
- 将 bootstrap 新默认值从 `strategy_lane == "directional"` 改为 `False`。

测试 T-101：

```text
首次 bootstrap
→ 将 simulation_sampling_fallback_enabled 改为 False
→ 再次 bootstrap
→ 仍为 False
```

测试 T-102：

```text
新建 directional run
→ 默认值为 False
```

提交：

```text
fix(S-101): preserve and disable sampling fallback by default
```

### S-102：新开仓杠杆设置失败必须 Fail Closed

对应问题：P1-005

修改：

- `services/execution/gateway.py`
- `tests/services/test_binance_gateway.py`

冻结行为：

1. 必须先判断 `close_only`。
2. 只有增加仓位的订单才需要设置杠杆。
3. 新开仓 `requested_leverage >= 1` 时：
   - 调用 `set_leverage()`。
   - 任意异常转换为：

```text
LEVERAGE_CONFIGURATION_FAILED: <original error>
```

   - 不得调用 `create_order()`。
4. ReduceOnly/CLOSE/REDUCE 不调用该新开仓杠杆前置步骤，不得阻塞平仓。
5. 不新增数据库字段，不新增错误状态机。沿用 `ensure_binance_execution()` 现有异常持久化路径。

最小代码结构：

```python
close_only = ...

if not close_only and requested_leverage >= 1:
    try:
        self.set_leverage(...)
    except Exception as exc:
        raise ValueError(
            f"LEVERAGE_CONFIGURATION_FAILED: {exc}"
        ) from exc
```

测试 T-201：

```text
set_leverage 抛异常
→ create_order 调用次数为 0
→ submit_order 抛出 LEVERAGE_CONFIGURATION_FAILED
```

测试 T-202：

```text
ReduceOnly 订单
→ 即使 set_leverage stub 会失败，也不调用 set_leverage
→ 平仓 create_order 继续执行
```

提交：

```text
fix(S-102): fail closed when entry leverage setup fails
```

### S-103：恢复历史价格漂移门槛

对应问题：P1-006

修改：

- `shared/config.py`
- 对应 pretrade 测试文件，优先使用现有 `tests/services/test_execution_truth.py` 或 gateway pretrade 测试

冻结值：

```python
pretrade_min_price_drift_bps = 20.0
pretrade_atr_drift_fraction = 0.25
```

不得使用高于：

```text
30bps / 0.25 ATR
```

的替代值。

测试 T-301：

```text
decision reference = 100
mark price = 100.50
ATR 条件不足以放宽到 50bps
→ PRETRADE_PRICE_DRIFT
```

测试必须证明该场景在 `100bps` 配置下会通过，在冻结值下会拒绝。

提交：

```text
fix(S-103): restore strict pretrade drift limits
```

### S-104：移除采样仓位的 price×0.0015 人为系数

对应问题：P1-003

修改：

- `services/execution/paper_signal.py`
- `tests/services/test_directional_sampling_fallback.py`
- 必要时 `tests/services/test_paper_signal.py`

冻结行为：

```python
requested_notional = float(min_notional)
```

不得使用：

```python
reference_price * 0.0015
```

不得新增另一个人为币价比例。

交易所数量精度和最小名义金额问题必须继续由：

- market rules
- `min_notional`
- `step_size`
- amount precision

处理。

测试 T-401：

```text
BTC 与 ETH 采样请求
→ requested_notional 分别等于各自交易所 min_notional
→ 不因币价高低自动形成 price×固定币数量的差异
```

提交：

```text
fix(S-104): remove price-linked sampling notional
```

### S-105：采样候选只能保留决策事实，不得拥有正式仓位权限

对应问题：P1-004

修改：

- `services/execution/paper_cycle_orchestrator.py`
- `tests/services/test_paper_runtime.py`
- `tests/services/test_directional_sampling_fallback.py`

冻结行为：

#### Flat 状态

如果：

```python
base_order.entry_context["testnet_sampling_mode"] is True
```

则：

- 允许保留 decision trace/action。
- 不调用 `gatekeeper.submit_order()`。
- 不调用 gateway。
- 不创建 PositionSnapshot/PositionRecord。
- action 使用明确名称，例如：

```text
skip_non_promotable_sampling
```

#### 已有正式仓位

如果当前有仓位，且本周期 `base_order` 来自 sampling：

- 仍可执行已有仓位的止损、止盈、时间退出和风险退出。
- sampling direction 不得进入 `opposite_signal` 条件。
- `rank_dropout` 行为保持不变。

最小判断：

```python
sampling_signal = bool(
    base_order.entry_context.get("testnet_sampling_mode")
    or base_order.entry_context.get("evidence_class")
       == "NON_PROMOTABLE_PIPELINE_SAMPLE"
)
```

flat 分支在构建 TradeIntent 和提交 Gatekeeper 之前终止。

opposite 条件：

```python
rank_dropout or (
    not sampling_signal
    and request.close_on_opposite_signal
    and current_position.side != base_order.direction
)
```

测试 T-501：

```text
flat + primary rejected + sampling LONG
→ 产生 skip_non_promotable_sampling
→ gateway.submit_order 未调用
→ 无订单/持仓
```

测试 T-502：

```text
正式 LONG + sampling SHORT
→ 不产生 opposite_signal close
→ ReduceOnly 未调用
→ 正式 LONG 仍存在
```

测试 T-503：

```text
正式 LONG + primary SHORT
→ 现有 opposite_signal 行为仍可工作
```

提交：

```text
fix(S-105): prevent sampling decisions from owning positions
```

阶段 1 Gate：

```bash
pytest -q \
  tests/services/test_directional_sampling_fallback.py \
  tests/services/test_binance_gateway.py \
  tests/services/test_paper_runtime.py \
  tests/services/test_execution_truth.py \
  tests/services/test_paper_bootstrap.py
```

阶段 1 通过后继续阶段 2。不得因为阶段 1 通过就声称仓位预设问题已全部解决。

---

## 5. 阶段 2：操作员配置成为唯一运行真源

该阶段是当前问题的必需修复，不是可选优化。没有完成 S-201 和 S-202，不得声称“页面预设失效已解决”。

### S-201：bootstrap 只填缺失默认值，不覆盖已保存 operator 字段

对应问题：P1-001

修改：

- `services/execution/bootstrap.py`
- `tests/services/test_paper_bootstrap.py`
- 必要时 `tests/api/test_paper_runtime_api.py`

操作员字段集合：

```python
OPERATOR_AUTO_SETTING_KEYS = (
    "risk_per_trade",
    "max_leverage",
    "order_notional_usdt",
    "max_open_positions",
    "max_symbol_exposure",
    "max_total_exposure",
    "daily_loss_limit",
    "weekly_loss_limit",
    "hard_stop_drawdown_limit",
    "asset_risk_tiers",
    "correlation_peer_threshold",
    "correlated_peer_count_limit",
    "correlated_cluster_exposure_limit",
    "net_directional_exposure_limit",
    "llm_veto_enabled",
    "market_intelligence_enabled",
    "simulation_sampling_fallback_enabled",
    "auto_settings_updated_at",
)
```

冻结合并规则：

1. 未存在 PaperRun：使用 bootstrap defaults。
2. 已存在 PaperRun：
   - bootstrap 可以刷新结构性/派生字段，例如 universe、runtime key。
   - 若 `previous` 中存在 operator key，则保留 previous。
   - 不要求值为 truthy；`False`、`0`、空列表等合法显式值必须保留。
3. 现有 Testnet authorization preserved 逻辑保持不变。
4. 不把整个 `previous` 无条件放到最后，避免保留已经需要刷新或撤销的结构字段。
5. 不新增第二套配置表。

建议实现：

```python
operator_preserved = {
    key: previous[key]
    for key in OPERATOR_AUTO_SETTING_KEYS
    if key in previous
}
profile = {
    **previous,
    **execution_profile,
    **operator_preserved,
    **authorization_preserved,
}
```

如果需要区分 bootstrap 默认和 operator save，使用已有：

```text
auto_settings_updated_at
```

但 `simulation_sampling_fallback_enabled` 必须无条件保留显式值。

测试 T-601：

保存以下自定义值：

```text
max_leverage=7
risk_per_trade=0.012
order_notional_usdt=123
max_symbol_exposure=0.11
max_total_exposure=0.33
simulation_sampling_fallback_enabled=False
```

重新 bootstrap，断言全部原样保留。

测试 T-602：

已有授权字段：

```text
cost_gate_verified
testnet_acceptance_verified_at
mirror_to_gateway
execution_mode
```

仍按旧合同保留或清理，不发生回归。

测试 T-603：

bootstrap 更新 `universe_assets` 等结构字段仍正常。

提交：

```text
fix(S-201): preserve operator auto settings across bootstrap
```

### S-202：仓位/杠杆计算以 execution_profile 为最高真源

对应问题：P1-002

修改：

- `services/execution/paper_signal.py`
- `tests/services/test_paper_signal.py`
- `tests/services/test_asset_risk_tiers.py`
- `tests/api/test_paper_runtime_api.py`

冻结优先级：

#### 杠杆

```text
execution_profile.asset_risk_tiers 中 symbol 对应 tier
→ execution_profile.max_leverage
→ strategy.position_rules.max_leverage
→ 1x
```

#### 仓位

本轮不新增 `sizing_mode`，不改变字段语义。`order_notional_usdt` 继续表示“名义仓位”，不是保证金。

```text
execution_profile.order_notional_usdt
→ strategy.position_rules.notional_usdt
→ strategy.position_rules.order_notional_usdt
→ execution_profile.risk_per_trade
→ strategy.position_rules.risk_per_trade
→ fallback equity fraction
```

风险上限仍应用：

- asset tier max position fraction
- `execution_profile.max_symbol_exposure`
- RiskProfile Gatekeeper

具体要求：

1. `_requested_leverage()` 在无 asset tiers 时，先读 profile。
2. `_requested_notional()` 优先读 profile `order_notional_usdt`。
3. profile `risk_per_trade` 存在时，不依赖 Strategy rules 是否含有同名键。
4. 不增加新的并行 sizing 函数。
5. 不将 `order_notional_usdt` 解释为 margin。
6. 保持当前 confidence multiplier 和 exposure cap 行为。

测试 T-701：

```text
profile.max_leverage=7
strategy.max_leverage=40
无 asset tiers
→ requested_leverage=7
```

测试 T-702：

```text
profile.order_notional_usdt=123
strategy rules 无 order_notional_usdt
→ requested_notional=123（再受既有 exposure cap 约束）
```

测试 T-703：

```text
profile.risk_per_trade=0.012
strategy.risk_per_trade=0.05
无 fixed notional
→ 使用 0.012
```

测试 T-704：

```text
profile asset_risk_tiers 为 symbol 指定 6x
profile.max_leverage=20
→ 使用 tier 6x
```

提交：

```text
fix(S-202): make operator profile authoritative for sizing
```

### S-203：保存 → 快照 → bootstrap → 下一周期合同测试

对应问题：P1-001、P1-002

修改：

- 优先新增到 `tests/api/test_paper_runtime_api.py`
- 或新增一个聚焦 legacy 配置合同的测试文件

流程：

```text
1. bootstrap directional PaperRun
2. 调用 auto-settings API 保存自定义值
3. 激活 NEXT_CYCLE snapshot
4. 再次 bootstrap
5. 再激活 pending snapshot
6. 构造一个 primary 非 sampling 开仓
7. 生成订单
```

断言：

```text
execution_profile 中自定义值未变化
active ConfigSnapshot execution_profile 未变化
requested_leverage 等于 operator 设置或 operator tier
requested_notional 等于 operator 固定名义金额/风险预算
config_snapshot_id 与订单一致
testnet_sampling_mode=False
```

测试 T-801 必须覆盖：

```text
max_leverage=7
order_notional_usdt=123
simulation_sampling_fallback_enabled=False
```

提交：

```text
test(S-203): lock operator sizing across restart and snapshot activation
```

---

## 6. 最终验证

定向测试：

```bash
pytest -q \
  tests/services/test_paper_bootstrap.py \
  tests/services/test_paper_signal.py \
  tests/services/test_directional_sampling_fallback.py \
  tests/services/test_binance_gateway.py \
  tests/services/test_asset_risk_tiers.py \
  tests/services/test_paper_runtime.py \
  tests/services/test_execution_truth.py \
  tests/api/test_paper_runtime_api.py
```

静态验证：

```bash
ruff check .
ruff format --check .
mypy
git diff --check
```

全量回归：

```bash
pytest -q -m "not integration"
```

安全验证：

- 不执行 Mainnet。
- 不执行真实资金订单。
- 不执行 natural Testnet acceptance。
- 如果必须做真实 Testnet 验证，必须由用户单独授权，本冻结方案不包含。

## 7. 明确禁止修改

- `services/automated_trading/application/entry_service.py`
- V2 `cycle_service.py`
- V2 `decision_service.py`
- V2 `binance_adapter.py`
- MACD/EMA/ADX
- 主策略候选
- 止盈止损策略
- 数据库迁移
- 前端 UI
- API 公共字段语义
- 依赖版本
- 无关格式

## 8. 停止条件

以下全部满足后停止：

1. S-000 已确认 actual writer，或明确按默认假设。
2. S-101～S-105 全部通过。
3. S-201～S-203 全部通过。
4. 定向测试、静态检查、全量非 integration 测试通过。
5. 没有修改 V2、DEGRADED 或策略买卖点。
6. 工作区只剩生成的实施报告。
7. 输出 `03-implementation-verification-report.md`。

不得以“还可以增加 sizing_mode”“再做杠杆读回”“继续优化买卖点”为理由扩项。

## 9. 后续但不在本轮

- 显式 `sizing_mode=margin|notional|risk_budget`
- 交易所杠杆 read-back 与 `LEVERAGE_MISMATCH`
- V2 Active 仓位合同
- MACD 与买卖点专项
