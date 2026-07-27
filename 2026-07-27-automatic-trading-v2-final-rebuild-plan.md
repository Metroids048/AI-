# 自动开平单链路 V2 一次性重构实施总方案

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` or `superpowers:executing-plans` to implement this plan task-by-task. Every checkbox is a separate verification step. Do not batch unrelated tasks. Do not weaken existing tests to fit the implementation.
>
> 目标仓库：`Metroids048/AI-`
> 日期：2026-07-27
> 建议保存路径：`docs/superpowers/plans/2026-07-27-automatic-trading-v2-final-rebuild.md`

**Goal:** 在不开放 Mainnet 的前提下，重建一条唯一、真实、可观察、可恢复的 Binance USDT-M Testnet 自动交易链路：闭合 K 线与实时行情 → 确定性候选 → 有边界的 AI 审阅 → 入场风控 → Binance 真实成交 → 本地成交投影 → Binance 真实保护单 → 自然退出/止损/止盈 → ReduceOnly 真实平仓 → 本地与交易所最终一致。

**Architecture:** 冻结现有 `paper_*` 混合链路，不再继续在两个千行级文件中叠加分支。新增独立的 `services/automated_trading/` 垂直模块，以事件驱动状态机、不可变交易所回执、交易所权威快照和单写入者 Scheduler 为核心。旧链路仅保留 Local Paper 和历史读取能力；V2 在 Shadow 模式验证后取得唯一 Testnet 写入权，完成切换后删除旧交易所写入入口。

**Tech Stack:** Python 3、Pydantic、SQLAlchemy/Alembic、Binance USDT-M Testnet、FastAPI、React/Vite、Pytest、现有 Scheduler/Lease/Fencing 基础设施。

---

# 0. 为什么必须按 V2 重建，而不能继续局部修补

## 0.1 已经通过测试复现的真实缺陷

当前关键源码已经通过失败测试复现过以下问题：

1. 没有确认交易所成交，也可能创建 `MANAGED_STRATEGY` 本地仓位。
2. Binance Gateway 缺失时，本地订单仍可能保持 `accepted`。
3. 对账失败时，默认空阻断集合可能被解释为“允许开仓”。
4. Entry Kill Switch 会阻止 ReduceOnly 降风险退出。
5. 请求 Testnet 但运行条件未武装时，Orchestrator 可能继续走本地 fill/open-position。
6. 过去的“Exchange-First 已通过”主要是 Fake Binance Adapter 或 Exchange Emulator，网络订单数为 0。
7. 过去的“自然策略已通过”最后仍然留有打开仓位，未证明自然自动平仓。
8. 当前 `paper_cycle_orchestrator.py` 超过 11 万字节，`paper_exchange_execution.py` 超过 6 万字节，职责继续堆叠会使任何修复都产生新的隐式分支。
9. 前端通过多个 API 拼装状态，并用 PaperRun 名称和候选数量猜测“当前自动运行实例”，不是从一个权威 Runtime Contract 读取。
10. `binance_simulation_first`、`mirror_to_gateway`、本地 Paper、Testnet Acceptance、自然策略执行等概念混在同一模型中，导致“模拟成交”“镜像订单”“真实成交”语义不唯一。

## 0.2 已经确认不是当前问题的内容

以下内容不得再次作为本轮修改目标，除非新的失败测试重新证明存在问题：

- 当前版本 CloseOnly 已与开仓最小名义金额补齐分支分开；不得凭旧判断再次修改。
- 使用闭合 K 线本身不是 Bug；问题是没有决策漏斗、实时提交前价格快照和执行价格漂移检查。
- AI 不应直接生成任意订单价格和数量；API 用量为 0 应通过可观察的调用链解决，而不是让 AI 获得无限交易权限。
- Testnet Acceptance 固定往返单只能证明基础设施连接，不能作为自然策略闭环证明。

## 0.3 本次选择的重构方式

### 方案 A：继续修改现有 `paper_*` 文件

拒绝。原因：

- 状态语义混乱已经是架构问题；
- 每次修补都会触碰多个共享状态；
- 旧测试大量依赖混合模式，容易为了兼容而保留错误分支；
- 无法可靠证明哪条路径是真实 Testnet，哪条路径是本地模拟。

### 方案 B：在原文件内大规模重构

不推荐。原因：

- 改动面过大，旧代码和新代码在同一文件内同时存在；
- 很难在实施期间维持可运行基线；
- Agent 容易继续复用旧私有函数，形成半新半旧链路。

### 方案 C：并行建立 V2 垂直链路，Shadow 验证后一次切换

**本方案采用。**

关键原则：

- 新链路使用新包、新表、新 API、新运行标识；
- 旧链路在切换前只接受安全补丁，不再增加业务功能；
- Shadow 阶段 V2 只产生决策和订单计划，不发送交易所订单；
- Active 阶段只有 V2 拥有 Testnet 下单权限；
- 回滚只关闭 V2 Entry，不重新激活旧写入者；
- 已打开的 V2 仓位始终由 V2 Recovery/Exit 路径管理到平仓，不能转交旧系统。

---

# 1. 范围、非目标和不可变约束

## 1.1 本轮范围

仅支持：

- Binance USDT-M Testnet；
- BTC/USDT、ETH/USDT；
- 单向持仓模式；
- 自动方向交易；
- 初版仅支持 Market Entry、Market ReduceOnly Exit；
- 交易所止损与止盈保护；
- Local Paper 独立模拟；
- Testnet Sampling 独立采样；
- Production Candidate 独立研究/验证；
- AI 市场审阅和候选审阅；
- Runtime Truth API 与前端可观察页面。

## 1.2 本轮明确不做

- 不开放 Binance Mainnet；
- 不支持多交易所；
- 不支持 Hedge Mode 双向持仓；
- 不支持自动接管无法确认归属的人工仓位；
- 不在 V2 首版支持限价入场、冰山、TWAP；
- 不让 LLM 直接生成 quantity、leverage、stop price、take-profit price；
- 不让 AI 阻止硬止损、保护失败紧急平仓或清算防护；
- 不把 Testnet Sampling 的交易结果写入正式策略晋升证据；
- 不迁移旧幽灵单为 V2 Managed Position；
- 不同时运行两个 Testnet 订单写入者。

## 1.3 全局不可变约束

1. `BINANCE_TESTNET` 模式没有真实成交回执时，数据库中不得存在 V2 Managed Position。
2. 本地 `INTENT_CREATED`、`SUBMITTING`、`ACKNOWLEDGED` 都不等于成交。
3. `FILLED` 必须有 `exchange_order_id`、至少一个 `trade_id`、正的 `filled_quantity`、正的 `average_fill_price`。
4. V2 本地仓位只是交易所成交事实的投影，不是独立真相。
5. 对账状态不是 `HEALTHY` 时，禁止所有新增 Entry。
6. 对账异常不得阻止 ReduceOnly 降风险退出。
7. 硬退出不依赖策略 Manifest、LLM、MetaLabel、Net Edge 或信号数据新鲜度。
8. 入场止损止盈的绝对价格必须在真实成交后，以 `average_fill_price` 重算。
9. 本地 Protection 只有取得交易所保护订单 ID 后才能标记为 `ACTIVE`。
10. 无法确认订单是否已提交时进入 `EXCHANGE_UNKNOWN`，先按 Client Order ID 对账，禁止盲目重复下单。
11. Scheduler Fencing Token 必须绑定到 Cycle、Intent 和订单提交。
12. 每根被评估的闭合决策 K 线必须产生一条终态 Decision Funnel 记录。
13. API 未接通或数据缺失时返回 `null/UNAVAILABLE`，不得用 `0`、假余额或默认在线状态代替。
14. Mainnet 配置不进入 V2 枚举；不是“默认关闭”，而是 V2 根本没有 Mainnet 执行实现。
15. 任何阶段没有满足验收门槛，禁止进入下一阶段。

---

# 2. 目标目录与职责边界

## 2.1 新增目录

```text
services/automated_trading/
├── __init__.py
├── domain/
│   ├── enums.py
│   ├── commands.py
│   ├── events.py
│   ├── receipts.py
│   ├── state.py
│   ├── candidates.py
│   └── invariants.py
├── application/
│   ├── cycle_service.py
│   ├── decision_service.py
│   ├── entry_service.py
│   ├── exit_service.py
│   ├── protection_service.py
│   ├── reconciliation_service.py
│   ├── recovery_service.py
│   ├── sampling_service.py
│   └── ai_review_service.py
├── infrastructure/
│   ├── models.py
│   ├── repository.py
│   ├── binance_adapter.py
│   ├── local_paper_adapter.py
│   ├── runtime_lock.py
│   └── market_snapshot_provider.py
└── observability/
    ├── decision_funnel.py
    ├── runtime_snapshot.py
    ├── evidence_bundle.py
    └── metrics.py
```

## 2.2 现有文件处理策略

### 冻结，不再增加功能

```text
services/execution/paper_cycle_orchestrator.py
services/execution/paper_exchange_execution.py
services/execution/paper_order_lifecycle.py
services/execution/paper_signal.py
```

允许的修改仅限：

- 保留已验证的幽灵单安全守卫；
- 添加 Legacy Deprecated 标记；
- 在切换阶段停止旧 Testnet 写入；
- 删除旧入口。

禁止继续在这些文件中增加：

- 新策略条件；
- 新 AI 分支；
- 新保护单算法；
- 新对账状态；
- 新 Testnet Sampling；
- 新前端字段。

### 复用但通过 Adapter 隔离

```text
services/execution/gateway.py
services/execution/scheduler_coordination.py
services/execution/order_normalizer.py
services/strategy_library/repository.py
```

V2 Application 不得直接调用旧 Orchestrator 或旧 Paper Lifecycle。

### 需要修改

```text
shared/models/__init__.py
apps/api/main.py
apps/api/routers/runs.py
services/execution/bootstrap.py
services/execution/scheduler.py
services/execution/tasks.py
frontend/admin/src/pages/PaperConsole.jsx
frontend/admin/src/hooks/useConsoleData.js
frontend/admin/src/components/RuntimePanels.jsx
frontend/admin/src/components/TradingConsolePanels.jsx
```

修改方式应是“切换到 V2 契约或标记 Legacy”，不是继续把 V2 逻辑塞回旧文件。

---

# 3. 核心领域模型

## 3.1 运行模式

```python
class AutomatedTradingMode(StrEnum):
    LOCAL_PAPER = "LOCAL_PAPER"
    BINANCE_TESTNET = "BINANCE_TESTNET"
```

禁止出现：

```text
binance_simulation_first
mirror_to_gateway
testnet-but-local-fill
```

## 3.2 运行状态

```python
class EngineActivation(StrEnum):
    DISABLED = "DISABLED"
    SHADOW = "SHADOW"
    ACTIVE = "ACTIVE"
```

语义：

- `DISABLED`：不评估、不提交；
- `SHADOW`：读取真实数据、生成候选、完成 Gate 和订单规范化，但不提交；
- `ACTIVE`：允许 V2 提交 Testnet 订单；
- 同一时刻只能有一个 `ACTIVE` Testnet Engine。

## 3.3 订单状态机

```text
INTENT_CREATED
  → PRETRADE_APPROVED
  → SUBMITTING
  → ACKNOWLEDGED
  → PARTIALLY_FILLED
  → FILLED
  → POSITION_PROJECTED
  → PROTECTION_PENDING
  → PROTECTED
  → EXIT_PENDING
  → EXIT_SUBMITTING
  → EXIT_ACKNOWLEDGED
  → EXIT_PARTIALLY_FILLED
  → CLOSED
```

失败与恢复状态：

```text
PRETRADE_REJECTED
EXCHANGE_REJECTED
EXCHANGE_UNKNOWN
PROTECTION_FAILED
RECOVERY_REQUIRED
EMERGENCY_CLOSE_PENDING
CANCELED
```

禁止转换示例：

```text
INTENT_CREATED → FILLED
ACKNOWLEDGED → POSITION_PROJECTED
EXCHANGE_REJECTED → FILLED
EXCHANGE_UNKNOWN → 新建第二个同逻辑订单
PROTECTION_PENDING → PROTECTED（没有交易所保护单 ID）
```

## 3.4 Strategy Candidate

策略层只输出相对风险计划，不输出陈旧绝对价格：

```python
class TradeCandidate(FrozenModel):
    candidate_id: str
    cycle_id: str
    strategy_id: str
    strategy_version: str
    lane: Literal["PRODUCTION", "TESTNET_SAMPLING"]
    symbol: Literal["BTC/USDT", "ETH/USDT"]
    side: Literal["LONG", "SHORT"]
    signal_candle_close_time: datetime
    signal_reference_price: Decimal
    confidence: Decimal
    stop_distance: Decimal
    take_profit_distance: Decimal
    max_entry_drift_bps: Decimal
    expires_at: datetime
    non_promotable: bool
```

执行层在成交后计算：

```text
LONG:
stop = average_fill_price - stop_distance
take = average_fill_price + take_profit_distance

SHORT:
stop = average_fill_price + stop_distance
take = average_fill_price - take_profit_distance
```

## 3.5 Exchange 回执

```python
class ExchangeOrderReceipt(FrozenModel):
    account_id: str
    symbol: str
    client_order_id: str
    exchange_order_id: str
    status: str
    requested_quantity: Decimal
    acknowledged_at: datetime
    raw_hash: str

class ExchangeFillReceipt(FrozenModel):
    account_id: str
    symbol: str
    client_order_id: str
    exchange_order_id: str
    trade_ids: tuple[str, ...]
    side: Literal["BUY", "SELL"]
    reduce_only: bool
    requested_quantity: Decimal
    filled_quantity: Decimal
    average_fill_price: Decimal
    commissions: tuple[CommissionRecord, ...]
    exchange_event_time: datetime
    received_at: datetime
    raw_hash: str
```

## 3.6 交易所权威快照

```python
class AuthoritativeAccountSnapshot(FrozenModel):
    account_id: str
    exchange_server_time: datetime
    received_at: datetime
    positions: tuple[AuthoritativePosition, ...]
    open_orders: tuple[AuthoritativeOrder, ...]
    recent_orders: tuple[AuthoritativeOrder, ...]
    recent_trades: tuple[AuthoritativeTrade, ...]
    source: Literal["BINANCE_TESTNET_REST", "BINANCE_TESTNET_STREAM"]
    complete: bool
```

`complete=False` 时不得用于解除 Entry Block。

---

# 4. 数据库 V2 设计

## 4.1 新迁移

创建：

```text
migrations/versions/0013_automated_trading_v2.py
```

同一任务必须同步当前 schema revision 常量和对应测试，避免再次出现“迁移已到 0013、代码仍写 0012”。

## 4.2 新表

### `automated_trading_cycles`

关键字段：

```text
cycle_id UUID PK
engine_id
mode
activation
scheduled_for
started_at
completed_at
status
scheduler_instance_id
fencing_token
deployment_sha
reconciliation_status
entry_enabled
failure_code
```

唯一约束：

```text
UNIQUE(engine_id, scheduled_for)
```

### `automated_trade_decisions`

```text
decision_id UUID PK
cycle_id FK
candidate_id
strategy_id
strategy_version
lane
symbol
signal_candle_close_time
terminal_stage
terminal_status
reason_code
trace JSON
created_at
```

唯一约束：

```text
UNIQUE(strategy_id, symbol, signal_candle_close_time, lane)
```

### `automated_trade_intents`

```text
intent_id UUID PK
cycle_id FK
decision_id FK
position_group_id
client_order_id
symbol
side
action
reduce_only
state
requested_quantity NUMERIC(38,18)
signal_reference_price NUMERIC(38,18)
max_entry_drift_bps NUMERIC(18,8)
stop_distance NUMERIC(38,18)
take_profit_distance NUMERIC(38,18)
fencing_token
config_snapshot_id
config_hash
created_at
updated_at
```

唯一约束：

```text
UNIQUE(client_order_id)
UNIQUE(decision_id, action)
```

### `exchange_order_receipts`

```text
receipt_id UUID PK
intent_id FK
account_id
exchange_order_id
client_order_id
status
requested_quantity NUMERIC(38,18)
acknowledged_at
raw_hash
raw_payload JSON
```

唯一约束：

```text
UNIQUE(account_id, exchange_order_id)
UNIQUE(account_id, client_order_id)
```

### `exchange_fill_receipts`

```text
fill_receipt_id UUID PK
intent_id FK
account_id
exchange_order_id
trade_id
filled_quantity NUMERIC(38,18)
fill_price NUMERIC(38,18)
commission NUMERIC(38,18)
commission_asset
exchange_event_time
received_at
raw_hash
```

唯一约束：

```text
UNIQUE(account_id, trade_id)
```

平均成交价由 repository 按所有 Fill Receipt 聚合，不接受调用方手写。

### `managed_positions_v2`

```text
position_group_id UUID PK
account_id
symbol
position_side
strategy_id
strategy_version
lane
entry_intent_id FK
entry_fill_receipt_id FK
exchange_entry_order_id
quantity NUMERIC(38,18)
average_entry_price NUMERIC(38,18)
status
ownership_status
opened_at
closed_at
last_reconciled_at
```

约束：

```text
BINANCE_TESTNET + MANAGED:
entry_fill_receipt_id NOT NULL
exchange_entry_order_id NOT NULL
quantity > 0
average_entry_price > 0
```

仅允许一个打开的：

```text
(account_id, symbol, position_side, ownership_status=MANAGED)
```

### `protection_orders_v2`

```text
protection_id UUID PK
position_group_id FK
protection_type STOP_LOSS | TAKE_PROFIT
client_order_id
exchange_order_id
trigger_price NUMERIC(38,18)
quantity NUMERIC(38,18) NULL
close_position BOOLEAN
state
last_exchange_update_at
failure_code
```

`ACTIVE` 的数据库前置条件：

```text
exchange_order_id IS NOT NULL
trigger_price > 0
```

### `reconciliation_runs_v2`

```text
reconciliation_id UUID PK
cycle_id FK
account_id
status HEALTHY | DEGRADED | UNAVAILABLE
snapshot_hash
exchange_position_count
local_position_count
mismatch_count
entry_blocked
error_code
started_at
completed_at
details JSON
```

### `recovery_incidents_v2`

```text
incident_id UUID PK
position_group_id NULL
intent_id NULL
severity
incident_type
state
attempt_count
last_error
entry_block_all
created_at
resolved_at
```

### `llm_invocations_v2`

```text
invocation_id UUID PK
cycle_id
decision_id NULL
stage MARKET_REVIEW | TRADE_REVIEW
provider
model
called
skip_reason
status
latency_ms
prompt_tokens
completion_tokens
total_tokens
request_hash
response_hash
error_code
created_at
```

## 4.3 不迁移旧幽灵单

旧数据只进入 Legacy Read Model。

切换时：

- 查询 Binance Testnet 真实仓位；
- 没有交易所仓位的旧本地 Position 不迁移；
- 有交易所仓位但不能证明策略归属的，写入 `EXTERNAL_QUARANTINED`；
- 只有能通过 Client Order ID、Order ID、成交和策略身份完整匹配的仓位，才允许建立 V2 Managed Position；
- 不允许按 symbol、价格接近或数量接近猜测归属。

---

# 5. 唯一 Cycle 顺序

V2 每个周期必须严格按以下顺序执行：

```text
1. 获取 Scheduler Lease 和 Fencing Token
2. 创建 automated_trading_cycle
3. 校验 Engine Activation 和部署版本
4. 同步 Binance Server Time 和 Market Rules
5. 拉取完整 Authoritative Account Snapshot
6. 执行 Reconciliation
7. 恢复 UNKNOWN / RECOVERY_REQUIRED / EMERGENCY_CLOSE_PENDING
8. 管理已有仓位：
   8.1 检查交易所保护单
   8.2 检查硬止损/止盈/时间退出/策略失效
   8.3 提交 ReduceOnly Exit
   8.4 对账退出结果
9. 若 Reconciliation 非 HEALTHY：结束 Entry 部分
10. 读取闭合 K 线和实时 Market Snapshot
11. 对每个标的运行 Decision Funnel
12. 生成 Production 或 Testnet Sampling Candidate
13. 可选执行 AI Trade Review
14. Entry Gate
15. Pre-submit 价格漂移和盘口检查
16. 创建 Intent 和确定性 Client Order ID
17. 提交 Binance Testnet Market Order
18. 按 Client Order ID / Order ID 确认成交
19. 写入 Fill Receipts
20. 投影 Managed Position
21. 以真实平均成交价计算保护价
22. 提交并确认 Binance Protection Orders
23. 执行周期末 Reconciliation
24. 生成 Runtime Snapshot 和 Evidence Event
25. 完成 Cycle
```

不可调整的顺序：

- Recovery 和 Exit 永远先于新 Entry；
- 没有健康对账不得进入步骤 10–22；
- 不能先写本地 Position 再等待 Binance；
- 不能先标记 Protection ACTIVE 再等待交易所订单；
- 周期末必须再次对账，不能只在周期开始对账。

---

# 6. Entry 设计

## 6.1 初版只支持 Market Entry

原因：

- 当前首要目标是证明自然闭环，不是优化挂单成交；
- 限价单会引入 Pending、过期、追价、部分成交和撤单竞态；
- 先让 Exchange-First、保护和退出稳定，再单独设计 Limit Entry V3。

## 6.2 Client Order ID

要求：

- 确定性；
- 同一 Intent 重试使用同一个 Client Order ID；
- 长度符合 Binance 限制；
- 可从本地反向解析 Engine/Intent 类型；
- 不包含策略密钥或敏感信息。

建议格式：

```text
A2E-{intent_hash_20}-{leg}
A2X-{intent_hash_20}-{leg}
A2S-{position_hash_18}
A2T-{position_hash_18}
```

测试必须验证：

- 长度；
- 字符集；
- 同一 Intent 稳定；
- 不同 Intent 不冲突；
- Entry/Exit/Stop/Target 不冲突。

## 6.3 提交超时语义

### 提交前失败

没有发出网络请求：

```text
state = EXCHANGE_REJECTED
reason = PRE_SUBMIT_FAILURE
```

### 发出请求后超时

不能判断交易所是否接收：

```text
state = EXCHANGE_UNKNOWN
```

处理：

1. 禁止用新 Client Order ID 重试；
2. 按原 Client Order ID 查询订单；
3. 查询近期订单和用户成交；
4. 若找到订单，按真实状态恢复；
5. 若连续完整快照确认不存在，再标记 `NOT_FOUND_CONFIRMED`；
6. 只有此时才能由 Recovery Service 决定重新提交。

## 6.4 部分成交

- 每条交易所 Trade 写入独立 Fill Receipt；
- 聚合当前 filled quantity 和 weighted average price；
- 只投影已确认的成交数量；
- 初版 Market Entry 等待终态或短超时后处理；
- 已有成交但订单未终态时，必须对已成交数量提供保护；
- 后续新增成交后，Protection Service 重新对齐保护覆盖；
- 不允许本地按 requested quantity 建立满额仓位。

---

# 7. Protection 设计

## 7.1 保护价格来源

策略输出：

```text
stop_distance
take_profit_distance
```

执行层使用：

```text
average_fill_price
tick_size
position_side
```

生成绝对价格。

## 7.2 几何校验

LONG：

```text
stop < average_fill_price < take_profit
```

SHORT：

```text
take_profit < average_fill_price < stop
```

价格必须按 tick size 向风险更安全方向取整。

## 7.3 状态

```text
PLANNED
SUBMITTING
ACKNOWLEDGED
ACTIVE
TRIGGERED
CANCELED
FAILED
UNKNOWN
```

`ACTIVE` 必须有 Binance Exchange Order ID。

## 7.4 保护失败升级

```text
第一次提交失败
→ 查询真实仓位
→ 用同一逻辑身份、新的保护尝试编号重试一次
→ 仍失败则立即 Market ReduceOnly 紧急平仓
→ 再次查询真实仓位
→ 仍未平则 EMERGENCY_CLOSE_PENDING
→ 全账户 Entry Block
→ 高优先级告警
```

任何异常不得使用 `suppress(Exception)` 静默吞掉。

## 7.5 Stop/TP 竞态

当一个保护单触发时：

1. 接收交易所更新；
2. 查询真实仓位；
3. 若已平，取消兄弟保护单；
4. 若部分平，只保留剩余数量对应保护；
5. 若取消兄弟订单时发现它也已触发，再次查询真实仓位；
6. 本地以交易所最终仓位为准；
7. 不因为本地先后顺序产生反向仓位。

---

# 8. Exit 设计

## 8.1 Entry Gate 与 Exit Gate 完全分离

```python
validate_entry(...)
validate_reduce_risk_exit(...)
```

### Entry Gate 检查

- Engine Active；
- Reconciliation Healthy；
- Manifest/OOS；
- 数据闭合和新鲜度；
- Candidate 有效期；
- Price Drift；
- Spread；
- 风险预算；
- 仓位上限；
- 相关性；
- Net Edge；
- 可选 AI 风险标记；
- Entry Kill Switch。

### Exit Gate 只检查

- 权威交易所仓位存在；
- side 确实减少仓位；
- `reduce_only=True`；
- quantity 大于 0；
- quantity 不超过权威仓位；
- Client Order ID 幂等；
- Fencing Token 有效；
- Gateway 可调用。

以下条件不得阻止硬退出：

- Manifest 失效；
- AI 不可用或否决；
- MetaLabel 不通过；
- 信号 K 线过期；
- Entry Kill Switch；
- 净 Edge 不足；
- 新闻风险事件。

## 8.2 平仓数量

```python
close_qty = min(requested_qty, authoritative_position_qty)
close_qty = floor_to_step_size(close_qty)
```

不得向上扩大。

## 8.3 Already Flat

交易所返回 ReduceOnly already flat 时：

1. 查询权威仓位；
2. 若确实为 0，视为幂等成功；
3. 关闭本地 Position；
4. 取消残余保护；
5. 记录 `ALREADY_FLAT_RECONCILED`；
6. 不将其记为普通失败。

## 8.4 退出类型

首版支持：

- HARD_STOP；
- TAKE_PROFIT；
- TIME_EXIT；
- STRATEGY_INVALIDATION；
- OPPOSITE_SIGNAL_CLOSE；
- PROTECTION_FAILURE_EMERGENCY；
- MANUAL_REDUCE_ONLY。

禁止同周期直接反手：

```text
先完整平旧仓
→ 周期末对账
→ 下一闭合决策 K 线才允许新方向 Entry
```

---

# 9. Reconciliation 与 Recovery

## 9.1 对账状态

```text
HEALTHY
DEGRADED
UNAVAILABLE
RECOVERY_REQUIRED
```

### HEALTHY

- 完整快照；
- 本地/交易所可解释一致；
- 允许 Entry。

### DEGRADED

- 快照可用，但存在非关键不一致；
- 默认阻止相关 symbol Entry；
- 允许 Exit 和恢复。

### UNAVAILABLE

- Gateway 缺失；
- REST 超时；
- 快照不完整；
- 解析异常；
- 账户身份不确定。

动作：

```text
阻止全部 Entry
保留 Exit
连续失败触发账户级 Entry Kill
```

### RECOVERY_REQUIRED

- 存在 UNKNOWN 订单；
- 本地 Managed Position 没有保护；
- 保护订单与仓位不一致；
- 交易所出现疑似 V2 Client ID 但本地没有记录。

## 9.2 仓位归属

优先级：

1. Position Group ID；
2. Client Order ID；
3. Exchange Order ID；
4. Fill Trade ID；
5. 持久化策略身份。

禁止只用：

- symbol；
- 数量接近；
- 价格接近；
- 时间接近。

无法证明时：

```text
ownership_status = EXTERNAL_QUARANTINED
entry_blocked_symbols += symbol
```

不得自动平仓，也不得自动继承旧保护。

## 9.3 重启恢复

进程启动后的第一个 Cycle：

1. 禁止 Entry；
2. 拉取完整账户快照；
3. 恢复所有 V2 Client Order ID；
4. 恢复 UNKNOWN Intent；
5. 检查所有 Managed Position 的保护；
6. 处理 Emergency Close Pending；
7. 完成健康对账后才能解除 Entry Block。

---

# 10. “一直不开单”的解决方式

## 10.1 Decision Funnel

每个 symbol、每根闭合决策 K 线必须记录以下阶段：

```text
CYCLE_STARTED
DATA_AVAILABLE
CANDLE_CLOSED
DATA_FRESH
TIMEFRAMES_ALIGNED
REGIME_EVALUATED
ENTRY_SIGNAL_EVALUATED
CANDIDATE_CREATED
META_LABEL_EVALUATED
MANIFEST_EVALUATED
RECONCILIATION_HEALTHY
RISK_APPROVED
AI_REVIEWED
PRICE_DRIFT_APPROVED
INTENT_CREATED
EXCHANGE_SUBMITTED
EXCHANGE_FILLED
POSITION_PROJECTED
PROTECTION_CONFIRMED
```

每个阶段：

```text
PASSED
SKIPPED
REJECTED
ERROR
```

稳定 Reason Code 示例：

```text
NO_ENTRY_SIGNAL
FOUR_HOUR_DIRECTION_CONFLICT
ONE_HOUR_REGIME_RANGE
RSI_OUTSIDE_RANGE
MACD_DIRECTION_MISMATCH
CANDIDATE_EXPIRED
MANIFEST_NOT_ELIGIBLE
RECONCILIATION_UNAVAILABLE
UNMANAGED_EXTERNAL_POSITION
RISK_LIMIT_EXCEEDED
PRICE_DRIFT_EXCEEDED
AI_PROVIDER_UNAVAILABLE
EXCHANGE_REJECTED
PROTECTION_FAILED
```

## 10.2 Production 与 Sampling 分离

### Production Lane

- 只允许已通过研究晋升的候选；
- 频率低可以接受；
- 结果进入策略证据。

### Testnet Sampling Lane

目的：

- 自然、高频地测试整个执行链；
- 不证明盈利；
- 不参与策略晋升；
- 使用相同 Exchange-First、保护、对账、退出链路。

初版规则：

```text
只用闭合 15m K 线

LONG:
close > EMA50
MACD histogram > 0
RSI ∈ [50, 72]
ATR14 > 0

SHORT:
close < EMA50
MACD histogram < 0
RSI ∈ [28, 50]
ATR14 > 0

stop_distance = max(1.2 × ATR14, fill_price × 0.0035)
take_profit_distance = 1.5 × stop_distance
```

额外限制：

- BTC/ETH；
- 每 symbol 最多一仓；
- 固定极小 Testnet 名义金额；
- 每 symbol 冷却；
- 每日最大交易数；
- 标记 `NON_PROMOTABLE_PIPELINE_SAMPLE`；
- AI Provider 故障不得阻止 Sampling，但必须记录。

这条 Lane 解决“长时间完全不开单无法验证链路”，但不会污染正式策略结论。

---

# 11. 实时价格与 K 线一致性

## 11.1 信号数据

- 指标只基于闭合 K 线；
- 保存 candle close proof；
- 保存 exchange event time 和 received time；
- 多周期必须按同一决策时点对齐；
- 不允许使用未来 K 线。

## 11.2 Pre-submit Snapshot

提交前必须读取：

```text
Binance server time
best bid
best ask
mark price
last price
spread
market rules
decision candle close time
decision age
ATR
```

## 11.3 价格漂移

```text
drift_bps = abs(mark_price - signal_reference_price)
            / signal_reference_price
            × 10000
```

Sampling 默认阈值：

```text
max(20 bps, 0.25 × ATR / signal_reference_price × 10000)
```

超限：

- 不追价；
- 记录 `PRICE_DRIFT_EXCEEDED`；
- 等下一根闭合 K 线重新判断。

小幅漂移：

- 允许提交；
- SL/TP 仍按实际成交价重算。

---

# 12. AI 集成边界

## 12.1 两类调用

### MARKET_REVIEW

定时运行，即使没有 Candidate 也执行，用于：

- 验证 API 真实接通；
- 汇总 4h/1h/15m 特征；
- 输出市场状态与风险标签；
- 保存 Token 用量。

### TRADE_REVIEW

仅在确定性 Candidate 已生成后运行：

输入：

- 结构化市场特征；
- Candidate；
- 当前仓位；
- Funding、波动率、市场风险；
- 不包含 API Key。

输出固定 Schema：

```json
{
  "bias": "support|neutral|oppose",
  "confidence": 0.0,
  "risk_flags": [],
  "summary": ""
}
```

## 12.2 权限

初版 AI 仅 Advisory：

- 不创建 Candidate；
- 不修改 quantity；
- 不修改 leverage；
- 不填写绝对 SL/TP；
- 不阻止硬退出；
- Sampling 中 Provider 失败时继续确定性执行；
- Production 中是否允许 AI 影响 Entry，必须由独立配置和测试控制。

## 12.3 可观察性

每次周期都必须有 LLM 记录：

- 已调用；
- 或未调用及原因；
- Provider；
- Model；
- Tokens；
- Latency；
- Error；
- Request/Response Hash。

API 用量为 0 时，前端必须能明确显示：

```text
API_KEY_MISSING
NO_CANDIDATE
MARKET_REVIEW_DISABLED
PROVIDER_ERROR
RATE_LIMITED
```

---

# 13. Runtime Truth API

## 13.1 新 Router

创建：

```text
apps/api/routers/automated_trading.py
```

前缀：

```text
/api/v2/automated-trading
```

## 13.2 端点

```text
GET  /runtime
GET  /cycles
GET  /decisions
GET  /orders
GET  /positions
GET  /protections
GET  /reconciliation
GET  /incidents
GET  /llm-invocations
GET  /evidence/latest
POST /controls/entry-disable
POST /controls/entry-enable
```

不提供 UI 中随意切换 Local Paper/Testnet Mirror 的开关。

Engine Activation 的修改必须：

- 需要管理员认证；
- 写入审计；
- 校验唯一写入者；
- ACTIVE 前验证 Testnet、安全边界、Scheduler、对账。

## 13.3 `/runtime` 返回

```json
{
  "engine": {
    "engine_id": "automated-trading-v2",
    "mode": "BINANCE_TESTNET",
    "activation": "ACTIVE",
    "entry_enabled": true,
    "mainnet_supported": false
  },
  "scheduler": {},
  "market_data": {},
  "exchange": {
    "source": "BINANCE_TESTNET",
    "timestamp": "...",
    "freshness": "FRESH",
    "positions": [],
    "open_orders": []
  },
  "local_projection": {
    "source": "V2_LOCAL_PROJECTION",
    "timestamp": "...",
    "positions": []
  },
  "reconciliation": {
    "status": "HEALTHY",
    "mismatches": []
  },
  "latest_decisions": [],
  "latest_incidents": [],
  "latest_llm_invocation": null
}
```

所有字段必须携带：

- source；
- observed_at；
- freshness；
- availability。

---

# 14. 前端改造

## 14.1 停止多接口猜测 Runtime

当前 `useConsoleData.js` 会：

- 拉取多个 API；
- 从 PaperRun 列表按名字、候选数量或最后一个运行猜选 autoRun；
- 混合 Binance Account、Paper Decision Trace、Local Overview；
- 接口失败时保留旧值。

V2 改为：

```text
一个 Runtime Snapshot
+ 明确的分页明细端点
+ SSE/WebSocket 增量事件
```

创建：

```text
frontend/admin/src/hooks/useAutomatedTradingRuntime.js
frontend/admin/src/api/automatedTrading.js
frontend/admin/src/components/AutomatedTrading/
```

## 14.2 页面

### Runtime Overview

显示：

- Engine Mode/Activation；
- Scheduler；
- 对账状态；
- Binance Testnet 连接；
- 最新 Cycle；
- Entry 是否被阻止；
- Mainnet 不支持。

### Why No Trade

显示每个标的最近一次：

- 决策 K 线时间；
- 终止阶段；
- Reason Code；
- 指标值；
- Gate 结果；
- 是否调用 AI；
- 是否进入交易所。

### Exchange vs Local

左右分栏：

- Binance 真实仓位；
- V2 本地投影；
- 差异；
- 归属；
- 最后对账时间。

### Orders

清楚区分：

```text
Intent
Exchange Order
Fill
Protection
Exit
```

不再把本地 accepted 显示成交易所订单。

### AI Calls

显示：

- Provider；
- Model；
- Called/Skipped；
- Tokens；
- Error；
- 最近调用时间。

## 14.3 禁止行为

- 不使用 `?? 0` 表示未知余额、价格、PnL；
- 不显示假 Online；
- 不把 Local Paper 仓位混入 Testnet Positions；
- 不把 Acceptance 往返单显示为策略交易；
- 不保留“Testnet 镜像开关”；
- 不使用 `paper_run_id` 猜测当前真实运行引擎；
- API 不可用显示“未接通/数据不可用/最后成功时间”。

---

# 15. 迁移与切换策略

## 15.1 Engine Selector

新增环境配置：

```text
AUTOMATED_TRADING_ENGINE=legacy|v2_shadow|v2_active
```

规则：

- `legacy`：旧系统维持现状；
- `v2_shadow`：V2 不发送订单；
- `v2_active`：V2 唯一允许 Testnet 写入；
- 任何模式下不得出现两个订单写入者。

## 15.2 Legacy Freeze Test

新增架构测试：

```text
test_legacy_execution_files_receive_no_new_business_dependencies
test_v2_does_not_import_paper_cycle_orchestrator
test_v2_does_not_import_paper_order_lifecycle
test_only_one_testnet_order_writer_is_active
```

## 15.3 切换前处理

1. 停止旧 Scheduler Entry；
2. 保留旧 ReduceOnly 安全退出直到仓位归零；
3. 查询 Binance 真实仓位、订单和成交；
4. 取消无法归属的旧策略保护订单；
5. 所有外部仓位进入 Quarantine；
6. 确认交易所没有旧 Managed Position；
7. 生成 Cutover Evidence Bundle；
8. 设置 `v2_active`；
9. V2 启动后先 Recovery/Reconciliation；
10. 健康后才开启 Entry。

## 15.4 回滚

回滚不是重新打开 Legacy Writer。

允许的回滚动作：

```text
v2 Entry Disabled
V2 Exit/Recovery 继续运行
新订单停止
已有仓位由 V2 管理到关闭
```

只有所有 V2 仓位归零、所有保护订单取消、完整对账健康后，才允许系统完全停机。

---

# 16. 测试分层

## 16.1 Unit

覆盖：

- 状态机；
- Client Order ID；
- 价格漂移；
- 数量取整；
- 止损止盈几何；
- Entry/Exit Gate；
- 归属；
- Reason Code；
- AI Schema。

## 16.2 Repository/DB

覆盖：

- 唯一约束；
- 事务；
- 幂等；
- Decimal 精度；
- Managed Position 成交凭证约束；
- Protection Active 约束；
- 重启恢复。

## 16.3 Strict Fake Exchange

Fake 必须模拟真实语义：

- ACK；
- Partial Fill；
- Filled；
- Reject；
- Timeout before request；
- Timeout after request；
- Duplicate Event；
- Out-of-order Event；
- Protection failure；
- Already Flat；
- REST unavailable；
- User Stream disconnect。

证据必须明确：

```text
scope = STRICT_FAKE
network_calls = 0
real_exchange_orders = 0
```

不得再命名成“真实链路已通过”。

## 16.4 Binance Testnet Contract

真实网络手动或受控运行：

- 账户权限；
- Server Time；
- Market Rules；
- Market Entry；
- Fill 查询；
- Stop/TP；
- ReduceOnly Exit；
- 订单取消；
- REST/User Stream 恢复。

证据必须包含真实：

```text
exchange_order_id
trade_id
server_time
account_id_hash
```

## 16.5 Natural Scheduler E2E

禁止调用 Acceptance 快捷脚本。

必须由普通 Scheduler 自然完成：

```text
Closed Candle
→ Candidate
→ Gate
→ Real Testnet Entry
→ Real Fill
→ Local Projection
→ Real Protection
→ Natural Exit Trigger
→ Real ReduceOnly Exit
→ Final Reconciliation
```

## 16.6 Soak

至少验证：

- 多个 Scheduler 周期；
- 重启；
- 无幽灵仓；
- 无重复 Entry；
- 无未保护 Managed Position；
- 无永久 UNKNOWN；
- 无 Exchange/Local 未解释差异。

计划中不以固定时长冒充质量；完成标准以事件数量、异常覆盖和最终一致性为准。

---

# 17. 分阶段任务

## Task 0：冻结基线和唯一设计源

**Files**

- Create: `docs/architecture/automated-trading-v2.md`
- Create: `docs/adr/ADR-001-automated-trading-v2-single-writer.md`
- Create: `docs/adr/ADR-002-exchange-first-receipts.md`
- Create: `docs/adr/ADR-003-entry-exit-gate-separation.md`
- Modify: `AGENTS.md`
- Test: `tests/contracts/test_automated_trading_architecture.py`

**Interfaces**

- Produces: 本计划中的目录、状态机、模式、表和切换策略成为唯一权威设计。
- Consumes: 已验证的五个幽灵单/退出回归测试。

- [ ] 将当前提交 SHA、配置快照、数据库 schema revision、已有失败证据归档。
- [ ] 在 `AGENTS.md` 写入：旧 `paper_*` 文件功能冻结，禁止新增执行逻辑。
- [ ] 新增架构测试，阻止 V2 导入旧 Orchestrator/Lifecycle。
- [ ] 运行：

```bash
pytest tests/contracts/test_automated_trading_architecture.py -v
```

- [ ] 提交：

```bash
git commit -m "docs: freeze legacy trading pipeline and define v2 boundaries"
```

**Gate 0**

- 设计文件不存在任何待定占位内容；
- 状态名称和数据库字段一致；
- 所有后续 PR 只引用这一份方案；
- 旧安全补丁测试保持绿色。

---

## Task 1：建立 V2 Immutable Contracts 与状态机

**Files**

- Create: `services/automated_trading/domain/enums.py`
- Create: `services/automated_trading/domain/commands.py`
- Create: `services/automated_trading/domain/events.py`
- Create: `services/automated_trading/domain/receipts.py`
- Create: `services/automated_trading/domain/state.py`
- Create: `services/automated_trading/domain/invariants.py`
- Test: `tests/services/test_automated_trading_state_machine.py`
- Test: `tests/contracts/test_automated_trading_contracts.py`

**Interfaces**

```python
reduce_execution_event(
    current: ExecutionAggregate,
    event: AutomatedTradingEvent,
) -> ExecutionAggregate

assert_managed_position_invariants(position, receipts) -> None
```

- [ ] 先写状态转换表的参数化失败测试。
- [ ] 验证 `INTENT_CREATED → FILLED` 必须失败。
- [ ] 验证没有 Fill Receipt 时 `POSITION_PROJECTED` 必须失败。
- [ ] 验证无 Exchange Order ID 的 Protection 不能 ACTIVE。
- [ ] 实现最小 reducer。
- [ ] 运行：

```bash
pytest tests/services/test_automated_trading_state_machine.py \
       tests/contracts/test_automated_trading_contracts.py -v
```

- [ ] 提交：

```bash
git commit -m "feat: add immutable automated trading v2 contracts"
```

**Gate 1**

- 状态机没有调用数据库或 Gateway；
- 所有非法转换被测试覆盖；
- 不复用旧字符串 `accepted/filled` 的含混语义。

---

## Task 2：建立 V2 数据库和 Repository

**Files**

- Create: `services/automated_trading/infrastructure/models.py`
- Create: `services/automated_trading/infrastructure/repository.py`
- Create: `migrations/versions/0013_automated_trading_v2.py`
- Modify: 数据库模型注册入口
- Modify: 当前 schema revision 常量
- Test: `tests/services/test_automated_trading_repository.py`
- Test: `tests/services/test_database_schema.py`

**Interfaces**

```python
class AutomatedTradingRepository:
    create_cycle(...)
    append_event(...)
    create_intent(...)
    save_order_receipt(...)
    save_fill_receipt(...)
    project_position(...)
    save_protection(...)
    record_reconciliation(...)
    record_incident(...)
```

- [ ] 先写“无 Fill Receipt 不能投影 Managed Position”数据库失败测试。
- [ ] 写 Client Order ID、Exchange Order ID、Trade ID 幂等测试。
- [ ] 使用 `Numeric`，不得用 Float 存储 V2 数量和价格。
- [ ] 实现迁移。
- [ ] 验证 SQLite 新建、升级和重复升级。
- [ ] 运行：

```bash
pytest tests/services/test_automated_trading_repository.py \
       tests/services/test_database_schema.py -v
```

- [ ] 提交：

```bash
git commit -m "feat: persist automated trading v2 execution facts"
```

**Gate 2**

- Migration revision 和代码 current revision 一致；
- 所有唯一约束有效；
- 旧表没有被破坏；
- 旧幽灵单不会自动导入 V2。

---

## Task 3：建立互斥运行模式和唯一写入者

**Files**

- Create: `services/automated_trading/infrastructure/runtime_lock.py`
- Modify: `services/execution/bootstrap.py`
- Modify: `services/execution/scheduler.py`
- Modify: `services/execution/tasks.py`
- Modify: 配置模型
- Test: `tests/services/test_automated_trading_engine_activation.py`

**Interfaces**

```python
resolve_engine_activation(settings) -> EngineActivationConfig
acquire_testnet_writer(engine_id, fencing_token) -> WriterLease
```

- [ ] 写两个 Engine 同时 Active 必须失败的测试。
- [ ] 写 Local Paper 不初始化 Binance Adapter 的测试。
- [ ] 写 Binance Testnet 不注入 Local Fill Adapter 的测试。
- [ ] 删除 V2 中 `mirror_to_gateway` 语义。
- [ ] 保留 Legacy 配置读取兼容，但转换为明确告警，不传入 V2。
- [ ] 运行：

```bash
pytest tests/services/test_automated_trading_engine_activation.py -v
```

- [ ] 提交：

```bash
git commit -m "feat: enforce a single automated trading order writer"
```

**Gate 3**

- Shadow 永不调用 submit；
- Active 只有一个 writer；
- Mainnet 无法配置；
- Testnet 未武装时显式 Block，不回退 Local Fill。

---

## Task 4：建立 Binance Testnet Adapter

**Files**

- Create: `services/automated_trading/infrastructure/binance_adapter.py`
- Create: `services/automated_trading/infrastructure/market_snapshot_provider.py`
- Reuse through adapter: `services/execution/gateway.py`
- Test: `tests/services/test_automated_trading_binance_adapter.py`

**Interfaces**

```python
class BinanceTestnetAdapter:
    fetch_authoritative_snapshot() -> AuthoritativeAccountSnapshot
    fetch_market_snapshot(symbol) -> PreSubmitMarketSnapshot
    submit_market_order(command) -> ExchangeOrderReceipt
    query_order_by_client_id(client_order_id) -> ExchangeOrderReceipt | None
    fetch_fills(exchange_order_id) -> tuple[ExchangeFillReceipt, ...]
    submit_protection(command) -> ExchangeOrderReceipt
    cancel_order(exchange_order_id) -> ExchangeOrderReceipt
```

- [ ] 先写 `_UnavailableBinanceClient` 必须返回明确不可用状态的测试。
- [ ] 禁止 Adapter 返回原本地 OrderExecution。
- [ ] 原始响应只作为 hash/审计保存，Application 只消费标准化回执。
- [ ] 测试 Binance Symbol、precision、step、tick 转换。
- [ ] 运行：

```bash
pytest tests/services/test_automated_trading_binance_adapter.py \
       tests/services/test_binance_gateway.py -v
```

- [ ] 提交：

```bash
git commit -m "feat: add authoritative binance testnet adapter"
```

**Gate 4**

- Adapter 不创建本地 Position；
- Adapter 不执行策略；
- Gateway 缺失是显式异常；
- 所有交易所身份字段可追溯。

---

## Task 5：建立 Reconciliation 和 Recovery

**Files**

- Create: `services/automated_trading/application/reconciliation_service.py`
- Create: `services/automated_trading/application/recovery_service.py`
- Test: `tests/services/test_automated_trading_reconciliation.py`
- Test: `tests/services/test_automated_trading_recovery.py`

**Interfaces**

```python
reconcile(snapshot, local_state) -> ReconciliationResult
recover_pending_state(snapshot, incidents) -> RecoveryResult
```

- [ ] 写 Gateway Timeout 阻止全部 Entry 的红灯测试。
- [ ] 写 UNAVAILABLE 仍允许 ReduceOnly 的测试。
- [ ] 写 UNKNOWN 按 Client Order ID 恢复的测试。
- [ ] 写外部仓位进入 Quarantine 的测试。
- [ ] 写进程重启恢复保护和 Emergency Close 的测试。
- [ ] 实现 Cycle 开始和结束两次对账。
- [ ] 运行：

```bash
pytest tests/services/test_automated_trading_reconciliation.py \
       tests/services/test_automated_trading_recovery.py -v
```

- [ ] 提交：

```bash
git commit -m "feat: add fail-closed reconciliation and recovery"
```

**Gate 5**

- 所有不可用路径 Entry Block；
- 无法归属的仓位不被自动接管；
- UNKNOWN 不会盲目重复提交；
- 重启先恢复再开仓。

---

## Task 6：建立 Decision Funnel 和 Candidate Contract

**Files**

- Create: `services/automated_trading/domain/candidates.py`
- Create: `services/automated_trading/application/decision_service.py`
- Create: `services/automated_trading/observability/decision_funnel.py`
- Adapt existing strategy functions; do not import old Orchestrator
- Test: `tests/services/test_automated_trading_decision_funnel.py`

**Interfaces**

```python
evaluate_symbol(context) -> DecisionOutcome
DecisionOutcome.candidate: TradeCandidate | None
DecisionOutcome.terminal_stage
DecisionOutcome.reason_code
```

- [ ] 每根闭合 K 线都必须有终态记录。
- [ ] 重复 K 线记录 `DUPLICATE_DECISION`，不能静默返回。
- [ ] 无信号、Regime 不匹配、MetaLabel、Manifest、Risk 分别使用不同 Reason Code。
- [ ] Candidate 只输出距离，不输出最终绝对保护价格。
- [ ] 运行：

```bash
pytest tests/services/test_automated_trading_decision_funnel.py -v
```

- [ ] 提交：

```bash
git commit -m "feat: make every automated trading decision observable"
```

**Gate 6**

- 用户能准确回答“为什么没开单”；
- Decision Service 是纯决策，不写 Exchange 状态；
- Candidate 与 Execution Intent 分离。

---

## Task 7：Entry Gate 和 Exchange-First Entry

**Files**

- Create: `services/automated_trading/application/entry_service.py`
- Create: `services/automated_trading/domain/commands.py`
- Reuse: `services/execution/order_normalizer.py` via explicit adapter
- Test: `tests/services/test_automated_trading_entry.py`

**Interfaces**

```python
evaluate_entry(candidate, runtime_context) -> EntryGateResult
execute_entry(candidate, gate_result, snapshot) -> EntryExecutionResult
```

- [ ] 写未健康对账不得 create intent 的测试。
- [ ] 写提交失败不创建 Position 的测试。
- [ ] 写提交后超时进入 UNKNOWN 的测试。
- [ ] 写重复 Cycle 不重复下单的测试。
- [ ] 写部分成交仅投影成交量的测试。
- [ ] 写真实 Fill Receipt 才能投影 Position 的测试。
- [ ] 运行：

```bash
pytest tests/services/test_automated_trading_entry.py -v
```

- [ ] 提交：

```bash
git commit -m "feat: implement exchange-first automated entries"
```

**Gate 7**

- 无交易所成交不可能出现 Managed Position；
- 本地 Intent 不展示成 Exchange Order；
- Timeout 后不会重复开仓；
- 成交价来自 Fill，不来自旧 K 线 close。

---

## Task 8：Protection Coordinator

**Files**

- Create: `services/automated_trading/application/protection_service.py`
- Test: `tests/services/test_automated_trading_protection.py`

**Interfaces**

```python
build_protection_plan(position, candidate, market_rules) -> ProtectionPlan
ensure_protection(position_group_id) -> ProtectionResult
```

- [ ] 写实际 Fill Price 重算保护价格的测试。
- [ ] 写 tick size 安全取整测试。
- [ ] 写无 Exchange Order ID 不得 ACTIVE 的测试。
- [ ] 写保护提交失败触发紧急平仓的测试。
- [ ] 写保护和紧急平仓均失败时全局 Entry Block 的测试。
- [ ] 写 Stop/TP 同时竞态的测试。
- [ ] 运行：

```bash
pytest tests/services/test_automated_trading_protection.py -v
```

- [ ] 提交：

```bash
git commit -m "feat: protect every exchange-confirmed position"
```

**Gate 8**

- 不存在“Managed 且未保护但系统健康”的状态；
- 保护异常全部持久化；
- 失败升级可在重启后继续。

---

## Task 9：ReduceOnly Exit Coordinator

**Files**

- Create: `services/automated_trading/application/exit_service.py`
- Test: `tests/services/test_automated_trading_exit.py`

**Interfaces**

```python
evaluate_exit(position, context) -> ExitDecision
execute_reduce_only_exit(decision, authoritative_position) -> ExitExecutionResult
```

- [ ] Entry Kill Switch 不阻止退出。
- [ ] AI 不被调用于硬退出。
- [ ] Manifest、数据过期、Net Edge 不阻止退出。
- [ ] quantity 不超过权威仓位。
- [ ] Already Flat 被对账为幂等成功。
- [ ] Partial Exit 只投影确认数量。
- [ ] 平仓后取消残余保护。
- [ ] 运行：

```bash
pytest tests/services/test_automated_trading_exit.py -v
```

- [ ] 提交：

```bash
git commit -m "feat: add fail-safe reduce-only automated exits"
```

**Gate 9**

- 所有降低风险路径独立于 Entry Gate；
- 自动平仓真实使用 ReduceOnly；
- 本地 CLOSED 只发生在交易所确认仓位归零后。

---

## Task 10：V2 Cycle Service 和 Scheduler 接管

**Files**

- Create: `services/automated_trading/application/cycle_service.py`
- Modify: `services/execution/scheduler.py`
- Modify: `services/execution/tasks.py`
- Test: `tests/services/test_automated_trading_cycle.py`
- Test: `tests/services/test_automated_trading_scheduler.py`

**Interfaces**

```python
run_automated_trading_cycle(request) -> AutomatedTradingCycleResult
```

- [ ] 按本计划第 5 节固定顺序写集成测试。
- [ ] 验证 Recovery/Exit 先于 Entry。
- [ ] 验证开始和结束两次对账。
- [ ] 验证 fencing token 过期时不提交。
- [ ] 验证两个 Scheduler 实例只有一个能写订单。
- [ ] 验证异常后 Cycle 有终态，不留 `running`。
- [ ] 运行：

```bash
pytest tests/services/test_automated_trading_cycle.py \
       tests/services/test_automated_trading_scheduler.py -v
```

- [ ] 提交：

```bash
git commit -m "feat: orchestrate automated trading v2 cycles"
```

**Gate 10**

- V2 可以在 Strict Fake 下完成自然 Entry→Protection→Exit；
- 所有动作可关联到同一 Cycle/Decision/Intent/Position Group；
- 旧 Orchestrator 不参与。

---

## Task 11：Testnet Sampling Lane

**Files**

- Create: `services/automated_trading/application/sampling_service.py`
- Create: `services/strategy_library/candidates/testnet_sampling_v2.py`
- Modify: Candidate Registry
- Test: `tests/services/test_testnet_sampling_v2.py`

**Interfaces**

```python
generate_sampling_candidate(closed_bars, cooldown_state) -> TradeCandidate | None
```

- [ ] 使用本计划第 10.2 节的确定性规则。
- [ ] 只使用闭合 K 线。
- [ ] Candidate 强制 `non_promotable=True`。
- [ ] 限制每日次数、冷却和单 symbol 一仓。
- [ ] Sampling 仍必须经过 Exchange-First、Protection、Reconciliation。
- [ ] 运行：

```bash
pytest tests/services/test_testnet_sampling_v2.py -v
```

- [ ] 提交：

```bash
git commit -m "feat: add non-promotable testnet sampling lane"
```

**Gate 11**

- 系统能产生足够测试机会；
- Sampling 不污染策略晋升；
- 不通过放宽安全门换取开单频率。

---

## Task 12：AI Review 和 Token 可观察性

**Files**

- Create: `services/automated_trading/application/ai_review_service.py`
- Modify: `services/agents/llm_runtime.py`
- Modify: `services/agents/llm_factory.py`
- Modify: `services/agents/service.py`
- Test: `tests/services/test_automated_trading_ai_review.py`
- Create: `scripts/smoke_automated_trading_llm.py`

**Interfaces**

```python
run_market_review(context) -> AIReviewResult
run_trade_review(candidate, context) -> AIReviewResult
```

- [ ] 写没有 Candidate 仍能运行 Market Review 的测试。
- [ ] 写每次调用或跳过都有 Invocation Record 的测试。
- [ ] 写 Sampling Provider 失败仍继续确定性流程的测试。
- [ ] 写 Forced Exit 从不调用 AI 的测试。
- [ ] 写 Token 用量持久化测试。
- [ ] Smoke 脚本只调用 LLM，不发送交易订单。
- [ ] 运行：

```bash
pytest tests/services/test_automated_trading_ai_review.py -v
```

- [ ] 提交：

```bash
git commit -m "feat: make automated trading ai reviews observable"
```

**Gate 12**

- API 用量不再靠猜；
- AI 权限没有扩张到订单数值；
- AI 故障不会制造幽灵单或阻止硬退出。

---

## Task 13：Runtime Truth API

**Files**

- Create: `apps/api/routers/automated_trading.py`
- Create: `shared/models/automated_trading.py`
- Modify: `shared/models/__init__.py`
- Modify: `apps/api/main.py`
- Test: `tests/api/test_automated_trading_runtime_api.py`

- [ ] 写 `/runtime` 不返回占位值的测试。
- [ ] 写 Exchange 和 Local Projection 分开返回的测试。
- [ ] 写 unavailable 使用 null 和状态，而不是 0 的测试。
- [ ] 写 Decision Reason、Incident、LLM Token 返回测试。
- [ ] 写控制端点认证和审计测试。
- [ ] 运行：

```bash
pytest tests/api/test_automated_trading_runtime_api.py -v
```

- [ ] 提交：

```bash
git commit -m "feat: expose a single automated trading runtime truth api"
```

**Gate 13**

- 前端不再需要猜 PaperRun；
- 一个 API 能解释真实账户、投影和差异；
- Legacy API 明确标记 deprecated。

---

## Task 14：前端替换占位与混合状态

**Files**

- Create: `frontend/admin/src/api/automatedTrading.js`
- Create: `frontend/admin/src/hooks/useAutomatedTradingRuntime.js`
- Create: `frontend/admin/src/components/AutomatedTrading/*`
- Modify: `frontend/admin/src/pages/PaperConsole.jsx`
- Modify: `frontend/admin/src/hooks/useConsoleData.js`
- Modify: `frontend/admin/src/components/RuntimePanels.jsx`
- Modify: `frontend/admin/src/components/TradingConsolePanels.jsx`
- Test: 对应 Vitest 文件

- [ ] 删除通过 PaperRun 名称猜当前 Auto Run 的逻辑。
- [ ] 删除 Testnet Mirror Toggle。
- [ ] Positions 默认显示 Binance Truth，Local Projection 单独显示。
- [ ] 未接通显示“未接通”，不显示 0。
- [ ] Why No Trade 显示阶段和 Reason Code。
- [ ] AI 页面显示 Tokens 和跳过原因。
- [ ] Acceptance 单标记为基础设施订单，不混入策略交易。
- [ ] 运行：

```bash
cd frontend/admin
npm test -- --run
npm run build
```

- [ ] 提交：

```bash
git commit -m "feat: render automated trading runtime truth in console"
```

**Gate 14**

- 页面所有运行值都有 source/time/freshness；
- 无 Fake Online、Mock Balance、Ghost Position；
- API 断线不会保留旧状态冒充实时状态。

---

## Task 15：Shadow 运行

**Files**

- Create: `scripts/run_automated_trading_shadow.py`
- Create: `scripts/audit_automated_trading_shadow.py`
- Test: `tests/services/test_automated_trading_shadow.py`

Shadow 必须：

- 使用真实市场数据；
- 使用真实 Binance 账户只读快照；
- 不提交订单；
- 生成 Candidate、Gate、Normalized Order、保护计划；
- 与旧系统决策并行比较，但不要求结果相同。

- [ ] 证明 `network_order_submit_calls == 0`。
- [ ] 统计每层漏斗通过率。
- [ ] 验证价格漂移和保护几何。
- [ ] 验证旧系统和 V2 的差异可解释。
- [ ] 运行：

```bash
pytest tests/services/test_automated_trading_shadow.py -v
python -m scripts.audit_automated_trading_shadow
```

- [ ] 提交：

```bash
git commit -m "test: validate automated trading v2 in shadow mode"
```

**Gate 15**

- Shadow 无订单；
- 每根 K 线可解释；
- 没有未处理异常；
- Runtime API 与前端展示一致。

---

## Task 16：真实 Binance Testnet Contract 验收

**Files**

- Create: `scripts/verify_automated_trading_testnet_contract.py`
- Create: `tests/integration/test_automated_trading_testnet_contract.py`
- Create: Evidence Schema

测试内容：

1. Preflight；
2. Server Time；
3. Market Rules；
4. 极小 Market Entry；
5. Order ID；
6. Trade ID；
7. Fill Receipt；
8. Stop/TP；
9. ReduceOnly Exit；
10. 最终仓位和订单归零。

明确标记：

```text
proof_type = TESTNET_CONTRACT
natural_strategy = false
```

- [ ] 默认 CI 跳过真实网络测试。
- [ ] 需要显式授权和 Testnet Credentials。
- [ ] Evidence 不保存密钥。
- [ ] 失败时执行补偿清理。
- [ ] 运行：

```bash
pytest -m testnet_contract tests/integration/test_automated_trading_testnet_contract.py -v
```

**Gate 16**

- 真实 Order ID、Trade ID；
- 本地从 Fill Receipt 投影；
- 真实保护和 ReduceOnly；
- 最终归零；
- 仍不能声称自然策略已打通。

---

## Task 17：自然 Scheduler E2E

**Files**

- Create: `scripts/verify_natural_automated_trading_cycle.py`
- Create: `tests/integration/test_natural_automated_trading_cycle_contract.py`

必须使用：

- 普通 Scheduler；
- Testnet Sampling 或 Production Candidate；
- 正常 Cycle；
- 正常保护或退出条件。

禁止：

- Acceptance Service；
- 手工开仓；
- Synthetic Local Fill；
- 直接调用 Entry/Exit Service 绕过 Scheduler；
- 强制修改数据库状态触发平仓。

Evidence 必须证明：

```text
cycle_id
decision_id
candidate_id
intent_id
entry exchange_order_id
entry trade_ids
position_group_id
stop/tp exchange_order_ids
exit trigger
exit exchange_order_id
exit trade_ids
final exchange position = 0
final local position = CLOSED
reconciliation = HEALTHY
```

**Gate 17**

只有这一 Gate 通过，才允许声称：

> Binance Testnet 自然自动开平单链路已打通。

---

## Task 18：Cutover 和 Legacy Writer 删除

**Files**

- Modify: `services/execution/scheduler.py`
- Modify: `services/execution/tasks.py`
- Modify: `apps/api/routers/runs.py`
- Modify: Legacy frontend controls
- Delete or disable old Testnet write call sites
- Test: `tests/contracts/test_single_testnet_writer_after_cutover.py`

- [ ] 停止旧 Entry。
- [ ] 确认旧仓位归零或 Quarantine。
- [ ] 保存 Cutover Evidence。
- [ ] 设置 `v2_active`。
- [ ] 验证旧 Testnet submit call site 不可达。
- [ ] 保留旧数据读取一段迁移期。
- [ ] 运行完整测试、CI、前端构建和 Hooks。
- [ ] 提交：

```bash
git commit -m "refactor: cut over to automated trading v2 single writer"
```

**Gate 18**

- 只有 V2 能提交自动 Testnet 订单；
- Legacy 不能通过 API、Scheduler 或配置重新武装；
- 回滚仅关闭 V2 Entry；
- 没有双写。

---

# 18. 故障注入矩阵

必须覆盖：

| 场景 | Entry | Exit | 本地状态 |
|---|---|---|---|
| Gateway 缺失 | 全阻止 | 记录不可用并告警 | 无幽灵单 |
| 提交前超时 | REJECTED | 可重试 | 无订单回执 |
| 提交后超时 | UNKNOWN | Recovery 查询 | 不重复提交 |
| 部分 Entry Fill | 仅投影成交量 | 保护成交量 | 不按请求量建仓 |
| User Stream 断线 | REST 对账 | REST 对账 | Entry Block 直到健康 |
| REST 对账失败 | 全阻止 | 保留降风险 | UNAVAILABLE |
| 保护提交失败 | 不再开新仓 | 紧急 ReduceOnly | Incident 持久化 |
| 紧急平仓失败 | 全账户 Entry Block | 重试/人工告警 | EMERGENCY_CLOSE_PENDING |
| Stop 已触发、取消失败 | 查询仓位 | 只处理剩余量 | 不反向开仓 |
| 两 Scheduler | 一个取得 Fencing | 一个拒绝 | 无重复订单 |
| 进程在 ACK 后崩溃 | Recovery 按 Client ID | 正常恢复 | 不重复 Entry |
| 进程在 Fill 后投影前崩溃 | Recovery 从 Trade 恢复 | 建仓并保护 | 无丢失仓位 |
| 本地数据库损坏 | 禁止 Entry | 交易所快照恢复/人工隔离 | 不猜归属 |
| 外部人工仓位 | 阻止同 symbol Entry | 不自动平仓 | EXTERNAL_QUARANTINED |
| AI Provider 失败 | Sampling 可继续 | 不影响 Exit | Invocation ERROR |
| 价格漂移超限 | SKIPPED | 不适用 | 明确 Reason |
| Protection 本地有、交易所无 | Entry Block | 重建或紧急平仓 | 不标健康 |

---

# 19. 完整验收命令

每一阶段都必须保存原始输出。

```bash
pytest tests/contracts/test_automated_trading_architecture.py -v
pytest tests/contracts/test_automated_trading_contracts.py -v
pytest tests/services/test_automated_trading_state_machine.py -v
pytest tests/services/test_automated_trading_repository.py -v
pytest tests/services/test_automated_trading_engine_activation.py -v
pytest tests/services/test_automated_trading_binance_adapter.py -v
pytest tests/services/test_automated_trading_reconciliation.py -v
pytest tests/services/test_automated_trading_recovery.py -v
pytest tests/services/test_automated_trading_decision_funnel.py -v
pytest tests/services/test_automated_trading_entry.py -v
pytest tests/services/test_automated_trading_protection.py -v
pytest tests/services/test_automated_trading_exit.py -v
pytest tests/services/test_automated_trading_cycle.py -v
pytest tests/services/test_automated_trading_scheduler.py -v
pytest tests/services/test_testnet_sampling_v2.py -v
pytest tests/services/test_automated_trading_ai_review.py -v
pytest tests/api/test_automated_trading_runtime_api.py -v
pytest -m "not integration" -v
ruff check .
ruff format --check .
mypy apps services shared scripts tests
pip-audit
cd frontend/admin && npm test -- --run && npm run build
python .claude/hooks/selftest.py
python scripts/sync_skill_copies.py --check
python scripts/refresh_current_state.py --run --check
```

真实网络验收单独执行：

```bash
pytest -m testnet_contract \
  tests/integration/test_automated_trading_testnet_contract.py -v

python -m scripts.verify_natural_automated_trading_cycle
```

---

# 20. 最终 Definition of Done

以下全部满足前，不得声称“改好了”或“链路已打通”。

## 20.1 代码

- [ ] V2 不导入旧 Orchestrator/Lifecycle。
- [ ] Local Paper 与 Binance Testnet 完全互斥。
- [ ] 没有通用函数能在 Testnet 无回执时直接 fill。
- [ ] Entry 和 Exit Gate 分离。
- [ ] 对账失败 Entry fail-closed。
- [ ] 无静默保护异常。
- [ ] 唯一 Testnet Writer。
- [ ] Mainnet 不可配置。

## 20.2 数据

- [ ] 每个 Managed Position 有 Fill Receipt。
- [ ] 每个 ACTIVE Protection 有 Exchange Order ID。
- [ ] 每个 Cycle 有终态。
- [ ] 每根评估 K 线有 Decision Funnel 终态。
- [ ] 每次 LLM 调用/跳过有记录。
- [ ] Exchange 和 Local 差异可解释。

## 20.3 真实 Testnet

- [ ] 普通 Scheduler 自然产生 Entry。
- [ ] Binance 返回真实 Order ID。
- [ ] Binance 返回真实 Trade ID。
- [ ] 本地从真实 Fill 投影。
- [ ] Binance 保护单真实存在。
- [ ] 正常退出条件触发。
- [ ] ReduceOnly 真实成交。
- [ ] 最终 Binance Position 为 0。
- [ ] 最终本地 Position 为 CLOSED。
- [ ] Reconciliation HEALTHY。
- [ ] 无人工 Acceptance 捷径。
- [ ] 无 Synthetic Local Fill。

## 20.4 前端

- [ ] 显示 Binance Truth 和 Local Projection。
- [ ] 幽灵差异显示红色告警。
- [ ] 为什么不开单可见。
- [ ] AI Token 用量可见。
- [ ] 未接通不显示 0。
- [ ] Acceptance 与策略交易分开。
- [ ] 不存在 Mirror Toggle。

---

# 21. 防返工执行纪律

1. 本计划是自动开平单 V2 唯一 Source of Truth；旧恢复计划只作为问题证据，不得与本计划同时执行。
2. 每个 Task 独立 PR/Commit，先写失败测试，再实现。
3. 每个 Task 完成后必须由另一上下文进行 Spec Review 和 Code Quality Review。
4. 禁止一次提交同时修改 Strategy、Execution、AI 和 Frontend。
5. 禁止为了通过测试修改测试中的正确断言。
6. 禁止在旧 `paper_*` 文件中“顺手修一下”新功能。
7. 每次发现新问题先归类：
   - 属于当前 Task：增加失败测试后修；
   - 属于后续 Task：记录到对应 Task，不提前实现；
   - 不在本轮范围：记录但不扩展。
8. 同一假设连续两次失败后停止打补丁，回到状态机和接口边界分析。
9. 不用测试数量作为完成证明；必须提交真实 Evidence。
10. 任何声称“真实链路已通”的报告必须自动检查：

```text
network_calls > 0
real_exchange_orders > 0
entry_trade_ids not empty
exit_trade_ids not empty
final_exchange_position == 0
final_reconciliation == HEALTHY
proof_type == NATURAL_SCHEDULER_TESTNET
```

11. 回滚只关闭 Entry，不允许恢复旧双写链路。
12. Cutover 前不删除旧数据；Cutover 后不允许旧代码重新获得写权限。

---

# 22. 建议执行顺序

```text
Task 0–3：锁定边界、状态机、数据库、唯一写入者
        ↓
Task 4–5：交易所 Adapter、对账和恢复
        ↓
Task 6–10：决策、Entry、Protection、Exit、Cycle
        ↓
Task 11–12：Sampling 和 AI
        ↓
Task 13–14：API 和前端
        ↓
Task 15：Shadow
        ↓
Task 16：真实 Testnet Contract
        ↓
Task 17：自然 Scheduler E2E
        ↓
Task 18：Cutover 和 Legacy Writer 删除
```

严格禁止跳过 Task 0–10，直接去“提高开单频率”或“接 AI”。

---

# 23. 最终架构判断

本次大改的核心不是把旧系统改得更复杂，而是删除四种歧义：

1. **模式歧义**：Local Paper 和 Binance Testnet 不再混合。
2. **状态歧义**：Intent、ACK、Fill、Position、Protection 各自拥有明确状态。
3. **真相歧义**：交易所是唯一执行真相，本地只是投影。
4. **验收歧义**：Fake、Acceptance、Shadow、Natural Testnet 使用不同 Proof Type，不能互相冒充。

执行完成后，系统应当只有一条自动 Testnet 链路：

```text
真实闭合数据
→ 可解释候选
→ 可观察 AI
→ Entry Gate
→ Binance Fill Receipt
→ V2 Managed Position
→ Binance Protection
→ ReduceOnly Exit
→ Binance/Local 健康对账
```

任何无法进入这条链路的订单都必须停留在明确的失败状态，而不是成为本地幽灵单。
