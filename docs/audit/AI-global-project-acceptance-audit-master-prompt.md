# AI- 全局项目验收审查与下一阶段决策 Master Prompt

> 使用场景：自动交易 V2 的五个收口 Gate 已由实施代理声称完成，需要对**整个项目当前真实完成度**进行独立、全局、生产入口级验收；根据验收结果，决定继续修复、进行真实 Testnet 验证，还是进入量化策略优化阶段。
>
> 本 Prompt 是“只审不改”的全局验收 Prompt。除 `audit/global_acceptance/` 下的审计证据外，禁止修改任何生产代码、测试、配置、迁移、文档或前端文件。
>
> 旧审计基线：`9afa16681e1525897ab03b89ad1febc37c30d807`
>
> 实际验收基线：运行时读取当前 `HEAD` 并锁定，不得预先假定。

---

# 一、角色与目标

你是一名独立的：

- 量化交易系统审计工程师；
- Python/FastAPI/SQLAlchemy/Celery 生产架构审查员；
- Binance USDT-M Testnet 执行与对账审查员；
- React 前端数据真实性审查员；
- 测试可信度和故障注入工程师；
- 量化研究框架审查员。

你不是实施代理，也不是代码修复代理。

你必须回答：

1. 五个收口 Gate 是否真的完成；
2. 自动开仓、保护单、自动平仓、恢复、对账是否从正式生产入口真实可达；
3. 数据库记录是否能证明交易所事实，而不是返回对象自称成功；
4. Legacy 是否正确隔离，是否存在双写；
5. Shadow、Testnet Contract、Natural Scheduler E2E 是否真实运行，而不是 Harness/Mock；
6. AI、Sampling、API、前端是否真正接入生产状态；
7. 整个项目是否已经具备进入量化策略优化阶段的工程基础；
8. 下一步应当是修复、继续真实环境验收，还是开始策略优化。

---

# 二、绝对规则

## 2.1 审计期间禁止修改

禁止修改：

```text
services/
apps/
shared/
frontend/
tests/
migrations/
scripts/
docs/
AGENTS.md
CURRENT_STATE.md
pyproject.toml
package.json
.env
```

只允许创建：

```text
audit/global_acceptance/<RUN_ID>/
```

若必须写临时探针：

- 放入审计目录或系统临时目录；
- 不加入项目正式测试目录；
- 不修改生产文件；
- 保留探针和原始输出作为证据。

## 2.2 不相信任何完成摘要

不得把以下内容直接当作完成证明：

- “Tasks 1–18 已完成”；
- “五个 Gate 全绿”；
- “1100+ tests passed”；
- “CI passed”；
- Commit message；
- README/CURRENT_STATE；
- 实施代理总结；
- 新文件存在；
- 类和函数存在；
- Fake Adapter 返回成功；
- Evidence Schema 校验通过；
- 真实测试默认 skipped。

必须自行沿生产调用图核查。

## 2.3 不允许 Mock 掉当前验证边界

以下审计必须使用真实生产入口或 Stateful Strict Fake：

- Scheduler 注册和 Task 分发；
- Engine Activation；
- Writer Lease/Fencing；
- Cycle 到 Repository；
- 数据库事务；
- Exit 执行；
- Recovery 执行；
- Runtime API 查询；
- Frontend 正式挂载；
- Shadow Adapter 构造和行情加载；
- Testnet Contract 命令构造；
- Natural E2E Observer。

若 Mock 了这些边界，该测试不能计入对应 Gate。

## 2.4 Proof Type 必须隔离

只允许以下 Proof Type：

```text
STATIC_CODE_REVIEW
UNIT
REPOSITORY_INTEGRATION
STRICT_FAKE_SCHEDULER_E2E
SHADOW_REAL_DATA
TESTNET_CONTRACT
NATURAL_SCHEDULER_TESTNET
FRONTEND_RUNTIME
STRATEGY_RESEARCH_READINESS
```

禁止：

- 用 `STRICT_FAKE` 声称 Testnet；
- 用 `TESTNET_CONTRACT` 声称自然策略；
- 用 `SHADOW` 声称订单执行；
- 用 Schema 测试声称 Collector 可运行；
- 用前端组件测试声称生产页面已接通。

## 2.5 跳过等于未完成

关键测试如被 skip、deselect、未启用、无凭据或无网络：

```text
状态 = BLOCKED_REAL_ENV
```

不得记为 PASS。

## 2.6 Mainnet 禁止

必须确认：

- Mainnet 无法从 V2 配置启用；
- API Key 只具备 Testnet 所需权限；
- 不允许 Withdrawal；
- 真实测试只能指向 Binance Testnet；
- 出现 Mainnet Endpoint 或真实资金风险立即终止验收。

## 2.7 审计不顺手修复

发现问题后：

- 记录最早断点；
- 记录影响；
- 记录最小修复边界；
- 不在本轮修改；
- 最终生成单独 `NEXT_ACTION_PROMPT.md`。

---

# 三、审计输出物

创建：

```text
audit/global_acceptance/<RUN_ID>/
├── BASELINE.json
├── ENVIRONMENT.md
├── CHANGESET.md
├── PRODUCTION_CALL_GRAPH.md
├── TEST_MATRIX.md
├── RAW/
├── PROBES/
├── DATABASE_EVIDENCE/
├── EXCHANGE_EVIDENCE/
├── FRONTEND_EVIDENCE/
├── STRATEGY_READINESS.md
├── FINDINGS.md
├── GLOBAL_ACCEPTANCE_REPORT.md
├── GLOBAL_ACCEPTANCE_MANIFEST.json
└── NEXT_ACTION_PROMPT.md
```

`RUN_ID`：

```text
YYYYMMDD-HHMMSS-<short-head-sha>
```

每个命令保存：

```text
命令
工作目录
环境变量名列表（不保存值）
开始时间
结束时间
退出码
stdout
stderr
```

---

# 四、最终结论枚举

最终只能输出一个：

```text
REJECTED_P0
REJECTED_INTEGRATION
BLOCKED_REAL_ENV
ACCEPTED_ENGINEERING_NOT_STRATEGY_READY
ACCEPTED_READY_FOR_STRATEGY_OPTIMIZATION
```

## REJECTED_P0

存在可能导致：

- 幽灵单；
- 重复单；
- 无保护仓位；
- 平仓被阻止；
- 本地错误关闭真实仓位；
- 双写入者；
- Mainnet 风险；
- 对账失败继续 Entry；
- 数据库事实丢失。

## REJECTED_INTEGRATION

安全设计可能正确，但：

- Scheduler 未接；
- Cycle 不落库；
- Exit/Recovery 不执行；
- API/前端未接；
- Sampling/AI 未接；
- Shadow/Contract/Natural 脚本不可用；
- Gate 未实际完成。

## BLOCKED_REAL_ENV

离线与 Stateful Fake 全部通过，但缺：

- Testnet Credentials；
- Binance 网络；
- 真实 Testnet Contract；
- Natural Scheduler Testnet Evidence；
- 浏览器运行证据。

## ACCEPTED_ENGINEERING_NOT_STRATEGY_READY

工程和真实 Testnet 链路通过，但策略研究证据、数据质量或统计方法不具备优化基础。

## ACCEPTED_READY_FOR_STRATEGY_OPTIMIZATION

同时满足：

- 工程链路通过；
- Natural Scheduler Testnet 通过；
- 数据质量、回测和研究框架无 P0 阻塞；
- 可以进入独立量化策略优化阶段。

---

# 五、Phase 0：锁定验收基线

## 5.1 仓库状态

运行：

```bash
git status --short
git branch --show-current
git rev-parse HEAD
git log -1 --date=iso --pretty=format:"%H%n%ad%n%s"
git remote -v
git rev-parse --verify 9afa16681e1525897ab03b89ad1febc37c30d807
```

要求：

- 工作区必须干净；
- 当前 HEAD 必须完整记录；
- 若工作区不干净，停止并输出 `REJECTED_INTEGRATION`；
- 不允许在脏工作区区分“本地修改”和“已完成 Gate”。

## 5.2 改动范围

运行：

```bash
git diff --stat 9afa16681e1525897ab03b89ad1febc37c30d807..HEAD
git diff --name-status 9afa16681e1525897ab03b89ad1febc37c30d807..HEAD
git log --oneline --decorate 9afa16681e1525897ab03b89ad1febc37c30d807..HEAD
```

分类：

```text
Domain
Database/Migrations
Scheduler/Tasks
Cycle
Adapter
Reconciliation/Recovery
Entry/Protection/Exit
Sampling
AI
API
Frontend
Scripts
Tests
Docs
Legacy
```

检查：

- 未授权策略修改；
- Legacy 新业务逻辑；
- 测试删除或弱化；
- `.skip`、`xfail`、marker 范围扩大；
- 删除安全检查；
- Mainnet 支持；
- Evidence 伪造或硬编码。

## 5.3 环境

记录：

```bash
python --version
pip --version
node --version
npm --version
pnpm --version
docker --version
```

仅记录以下环境变量是否存在，不记录值：

```text
AUTOMATED_TRADING_ENGINE
V2_TESTNET_CONTRACT_ENABLED
NATURAL_E2E_ENABLED
BINANCE_API_KEY
BINANCE_API_SECRET
BINANCE_HTTPS_PROXY
HTTPS_PROXY
PAPER_CONSOLE_DISABLE_LIVE_WS
DATABASE_URL
```

---

# 六、Phase 1：生产调用图审查

## 6.1 启动入口

从以下入口追踪：

```text
一键启动.cmd
PowerShell 启动脚本
API 启动命令
Scheduler 启动命令
Celery Task 注册
RuntimeScheduler 注册
```

回答：

1. API 和 Scheduler 是否进程隔离；
2. 是否可能启动两个 Scheduler；
3. Engine Activation 在哪个入口解析；
4. `legacy`、`v2_shadow`、`v2_active` 分别注册哪些 Task；
5. `v2_active` 是否完全移除 Legacy Exchange Writer；
6. `v2_shadow` 是否保证零 Write；
7. 未识别配置是否 fail-closed；
8. `.env.example` 是否包含必要配置；
9. 启动日志是否明确输出 Engine 和 Writer。

## 6.2 V2 正式调用链

从 Scheduler/Task 追到：

```text
Scheduler
→ V2 Task
→ Writer Lease/Fencing
→ Cycle Service
→ Runtime Control
→ Exchange Snapshot
→ Local State
→ Reconciliation
→ Recovery
→ Existing Position Management
→ Decision
→ Sampling/Production Candidate
→ AI Review
→ Entry Gate
→ Pre-submit Snapshot
→ Entry
→ Order Receipt
→ Fill Receipts
→ Position Projection
→ Protection
→ Final Reconciliation
→ Runtime Query/API
→ Frontend
```

每条边标记：

```text
PRODUCTION_CALL
TEST_ONLY
SCRIPT_ONLY
UNREACHABLE
```

任何核心节点只有 `TEST_ONLY` 或 `SCRIPT_ONLY`：

```text
Gate = FAIL
```

## 6.3 Legacy 隔离

检查：

- Legacy `paper_*` 是否 FROZEN；
- V2 是否导入 Legacy Orchestrator/Lifecycle；
- `v2_active` 是否仍能调用 Legacy Gateway Writer；
- Legacy 前端控制是否仍能武装 V2；
- Mirror Toggle 是否已从 V2 页面删除；
- Legacy 数据是否可能被 V2 当作 Managed Position。

---

# 七、Phase 2：数据库事实链和状态机

## 7.1 Schema

检查：

```text
Cycle
Decision
Intent
Exchange Order
Exchange Fill
Managed Position
Protection
Reconciliation
Incident
Runtime Control
LLM Invocation
Scheduler Lease
Sampling State
```

确认：

- 外键；
- Numeric 精度；
- 唯一 Client Order ID；
- 唯一 Exchange Order ID；
- 唯一 `(account_id, trade_id)`；
- 打开仓位 Partial Unique Index；
- ACTIVE Protection 必须有 Exchange Order ID；
- Managed Testnet Position 必须有 Fill；
- Reconciliation Enum 与数据库一致；
- Runtime Control 重启保留；
- Sampling cooldown 重启保留。

## 7.2 状态机动态反例

使用真实 Repository 和临时数据库执行：

```text
FILLED → INTENT_CREATED
CLOSED → POSITION_PROJECTED
ACTIVE → PLANNED Protection
无 Fill 创建 Managed Position
无 Exchange ID 创建 ACTIVE Protection
重复 Trade ID
重复 Exchange Order ID
同 symbol/direction/mode 第二个打开仓位
Order/Intent 不匹配
Fill/Order 不匹配
```

预期全部被拒绝。

## 7.3 完整事实链探针

通过正式 Scheduler Task + Stateful Strict Fake 触发一笔 Entry。

Task 完成后关闭 Session，重新打开数据库查询：

```text
Cycle = 1
Decision ≥ 1
Intent = 1
Exchange Entry Order = 1
Entry Fill ≥ 1
Managed Position = 1
Protection Order ≥ 1
Execution Events > 0
Start Reconciliation = 1
Final Reconciliation = 1
```

要求所有记录通过以下字段关联：

```text
cycle_id
decision_id
intent_id
position_group_id
client_order_id
exchange_order_id
trade_id
```

禁止用 Cycle 返回对象代替数据库查询。

---

# 八、Phase 3：Scheduler、单写入者和幂等

## 8.1 Engine 注册矩阵

分别验证：

| 模式 | Legacy Writer | V2 Shadow | V2 Writer |
|---|---:|---:|---:|
| legacy | 1 | 0 | 0 |
| v2_shadow | 按设计 | 1 | 0 |
| v2_active | 0 | 0 | 1 |
| invalid | 0 | 0 | 0 + Fatal |

## 8.2 双 Scheduler

并发启动两个 Scheduler 或并发调用同一 Scheduled Bar。

必须证明：

```text
只有一个取得 Lease
只有一个 Fencing Token 有效
只有一次 Entry Submit
另一个明确 SKIPPED/LEASE_DENIED
```

不得只检查内存锁。

## 8.3 重启幂等

场景：

1. ACK 前崩溃；
2. ACK 后保存前崩溃；
3. Fill 后 Position Projection 前崩溃；
4. Protection ACK 后保存前崩溃；
5. Exit ACK 后 Fill 前崩溃。

每个场景：

- 重启正式 Scheduler；
- 使用原 Client Order ID 恢复；
- 不产生重复 Entry/Exit；
- 本地最终与交易所一致；
- UNKNOWN 有终态。

---

# 九、Phase 4：行情、信号和不开单可解释性

本 Phase 不优化策略，只审查链路是否使用正确数据。

## 9.1 行情来源

检查：

- WebSocket 是否开启；
- 无代理时是否降级 REST；
- 降级是否有明确日志和 Runtime 状态；
- K 线是否来自预期数据源；
- 是否只使用闭合 K 线；
- 4h/1h/15m 是否 point-in-time 对齐；
- 时区和 Server Time 是否一致；
- 数据过期是否阻止 Entry；
- 数据过期是否不阻止硬 Exit。

## 9.2 Decision Funnel

对 BTC/ETH 多个闭合 K 线运行正式 Shadow/Scheduler。

每个 symbol/bar 必须有终态：

```text
PASSED
SKIPPED
REJECTED
ERROR
```

每个非 PASS 必须有稳定 Reason Code。

统计每层：

```text
评估次数
通过次数
拒绝次数
错误次数
主要拒绝原因
```

不得只显示“暂无交易”。

## 9.3 Sampling 与 Production 分离

确认：

- Sampling 真正进入正式 Cycle；
- `non_promotable=true`；
- 不写 Production Manifest；
- Cooldown/每日次数重启保持；
- Sampling 仍经过 Exchange-First、Protection、Exit、Reconciliation；
- Production Candidate 仍要求正式证据；
- 不通过放宽安全门增加开单频率。

---

# 十、Phase 5：Entry、Protection、Exit、Recovery 故障矩阵

必须使用 Stateful Strict Fake，从正式 Scheduler 入口运行。

## 10.1 Entry

覆盖：

```text
正常 ACK + Fill
Exchange Reject
Pre-submit Timeout
Post-submit Timeout
Partial Fill
Duplicate Fill Event
Out-of-order Event
Price Drift Exceeded
Min Notional
Step/Tick Size
Existing Position
Runtime Entry Disabled
Reconciliation Unavailable
Local DB Unavailable
```

断言：

- 无 Fill 无 Managed Position；
- Post-submit Timeout 使用原 Client ID 查询；
- Partial Fill 只投影确认数量；
- DB 不可用禁止 Entry；
- API Entry Disable 真正阻止 Scheduler Entry；
- Reconciliation 非 HEALTHY 禁止 Entry。

## 10.2 Protection

覆盖：

```text
Stop ACK
TP ACK
Stop 失败一次后重试
Protection 持续失败
Emergency ReduceOnly 成功
Emergency ReduceOnly 失败
Stop/TP 同时竞态
Process Restart
```

断言：

- 保护价格基于真实平均成交价；
- ACTIVE 必须有 Exchange ID；
- Protection 失败不能保持系统 HEALTHY；
- Emergency Close 失败持久化 Incident；
- 失败后全局 Entry Block；
- 重启继续 Recovery。

## 10.3 Exit

覆盖：

```text
Hard Stop
Take Profit
Time Exit
Strategy Invalidation
Manual ReduceOnly
Partial Exit
Already Flat
Already Flat 后 snapshot 失败
Protection 已触发时取消失败
```

断言：

- 正式 Cycle 调用 ReduceOnly；
- Entry Kill Switch 不阻止硬 Exit；
- AI/Manifest/Data Freshness 不阻止硬 Exit；
- 平仓数量不超过权威仓位；
- Snapshot 不可读时不得本地 CLOSED；
- 权威数量为 0 后才 CLOSED；
- 最终取消残余保护。

## 10.4 Reconciliation 和 Ownership

覆盖：

```text
完整一致
交易所仓位存在、本地缺失
本地仓位存在、交易所缺失
外部人工仓位
孤儿 A2E/A2X/A2S/A2T 订单
Symbol BTC/USDT:USDT
User Stream Disconnect
REST Snapshot Failure
Incomplete Snapshot
```

断言：

- 自有仓位可通过 Order/Trade/Position Group 认领；
- 外部仓位 Quarantine，不自动平；
- A2 Client ID 全部可识别；
- Symbol 规范化；
- Snapshot 不完整不解除 Entry Block；
- Cycle 开始和结束都对账。

---

# 十一、Phase 6：AI 和 Token 可观察性

## 11.1 生产调用

确认：

```text
Scheduler → V2 Market Review
Candidate → V2 Trade Review
```

不能只有测试调用。

## 11.2 每周期记录

每个相关周期必须存在：

```text
called=true
```

或：

```text
called=false
skip_reason=<明确原因>
```

保存：

```text
provider
model
latency
prompt_tokens
completion_tokens
total_tokens
request_hash
response_hash
error
```

## 11.3 权限边界

确认 AI：

- 不直接生成 quantity；
- 不设置 leverage；
- 不直接输出最终绝对 SL/TP；
- 不阻止硬退出；
- Sampling Provider 失败可按配置继续；
- Production Provider 失败行为明确；
- API Key 缺失不静默。

## 11.4 真实 Smoke

若 API Key 存在：

- 执行只读 LLM Smoke；
- 不提交订单；
- 查询数据库 Invocation；
- 查询响应 usage；
- 前端显示一致。

若无 Key：

```text
AI 真实调用 = BLOCKED_REAL_ENV
```

但必须验证 skip reason。

---

# 十二、Phase 7：Runtime API 和前端真实性

## 12.1 API

检查 API 是否查询：

```text
Repository
Runtime Controls
Scheduler Lease
Latest Reconciliation
Exchange Snapshot Cache
Decision Funnel
Incidents
LLM Invocations
```

禁止：

```text
进程内 _runtime_state 作为真相
模块导入时固定时间
默认 0 冒充未知
```

重启 API 后：

- Runtime Control 仍存在；
- Position/Decision/LLM 仍可查询；
- 不丢状态。

## 12.2 控制闭环

执行：

```text
entry-disable API
→ 数据库 Runtime Control
→ Scheduler 下周期读取
→ Entry 被阻止
→ Runtime API 显示阻止原因
→ 前端显示一致
```

恢复时：

```text
entry-enable API
→ 需要权限和审计记录
→ 只有 Reconciliation HEALTHY 才允许恢复
```

## 12.3 前端正式挂载

检查：

- 正式控制台是否挂载 V2 组件；
- 新 hook 是否被正式页面调用；
- Legacy 与 V2 是否明确区分；
- Mirror Toggle 是否从 V2 移除；
- 为什么不开单是否显示真实 Reason；
- Exchange 与 Local 是否分栏；
- AI Token 是否显示；
- API unavailable 是否显示“未接通”；
- 不保留旧缓存冒充实时数据。

## 12.4 浏览器验收

运行：

```bash
npm --workspace frontend/admin run test
npm --workspace frontend/admin run build
npm --workspace frontend/admin run dev
```

检查：

- React Console 0 Error；
- Network 没有请求风暴；
- 轮询/SSE 频率符合设计；
- 页面字段来源、时间、新鲜度正确；
- API 断开后的降级正确。

保存截图和 Console/Network 摘要。

---

# 十三、Phase 8：静态质量、CI、运维和安全

## 13.1 后端

运行：

```bash
pytest -m "not testnet_contract and not natural_e2e" -q
ruff check .
ruff format --check .
mypy apps services shared scripts tests
pip-audit
python .claude/hooks/selftest.py
python scripts/sync_skill_copies.py --check
python scripts/refresh_current_state.py --run --check
```

必须列：

```text
passed
failed
skipped
deselected
warnings
```

每个 skip 分类：

```text
真实网络
缺依赖
错误配置
未实现
可接受环境差异
```

## 13.2 前端

运行：

```bash
npm --workspace frontend/admin run test
npm --workspace frontend/admin run build
npm audit
```

## 13.3 Docker/迁移/重启

验证：

- 空数据库升级；
- 旧数据库升级；
- 重复升级；
- 失败回滚；
- API 重启；
- Scheduler 重启；
- 两实例冲突；
- 数据库暂时不可用；
- Proxy/WS 降级；
- 日志轮转；
- Evidence 不含密钥。

## 13.4 密钥与权限

搜索：

```text
API Key
Secret
Token
Password
Private Key
Database file
Session file
```

确认：

- 未提交真实密钥；
- Testnet Key 无 Withdrawal；
- Mainnet Endpoint 不可用；
- 日志和 Evidence 已脱敏。

---

# 十四、Phase 9：Shadow 验收

只有 Phase 0–8 无 P0 才能运行。

设置：

```text
AUTOMATED_TRADING_ENGINE=v2_shadow
```

必须通过正式 Scheduler，不直接调用 Cycle。

连续多个闭合 K 线周期确认：

```text
真实 Market Data
真实 Binance 只读账户快照
Decision Funnel 持久化
Sampling/Production Candidate 可解释
AI Invocation/Skip 可见
Exchange Write Calls = 0
Managed Position Created = 0
Protection Created = 0
```

Shadow 中任何写尝试：

```text
REJECTED_P0
```

---

# 十五、Phase 10：真实 Binance Testnet Contract

只有 Shadow PASS 才能运行。

## 15.1 Preflight

确认：

```text
Testnet Endpoint
Account Identity Hash
Server Time
Market Rules
当前仓位
当前 Open Orders
余额
API 权限
无 Mainnet
```

若存在无法归属仓位或订单，停止。

## 15.2 Contract

使用极小 Testnet 名义金额：

```text
Market Entry
ACK
Fill
Stop/TP
查询保护存在
ReduceOnly Exit
Fill
查询仓位归零
取消残余 V2 Orders
最终对账
```

必须获得真实：

```text
Entry Exchange Order ID
Entry Trade IDs
Stop/TP Exchange Order IDs
Exit Exchange Order ID
Exit Trade IDs
Final Exchange Qty = 0
Final Open V2 Orders = 0
```

Proof：

```text
TESTNET_CONTRACT
natural_strategy=false
```

Contract 失败后必须补偿清理并验证账户最终状态。

---

# 十六、Phase 11：Natural Scheduler Testnet E2E

只有 Contract PASS 才能运行。

## 16.1 禁止捷径

禁止：

- Acceptance 往返；
- 直接调用 Entry/Exit Service；
- 手写 Candidate；
- 修改数据库状态；
- Synthetic Fill；
- 手工平仓；
- 手工触发保护。

## 16.2 必须自然完成

```text
普通 Scheduler
→ 实时闭合 K 线
→ Sampling 或 Production Candidate
→ AI Review/Skip
→ Entry Gate
→ Binance Entry
→ Fill
→ 数据库 Position
→ Binance Protection
→ 自然 Stop/TP/Time Exit
→ ReduceOnly Exit
→ Exit Fill
→ Exchange Qty 0
→ Local CLOSED
→ Final Reconciliation HEALTHY
```

## 16.3 Evidence

必须串联：

```text
Deployment SHA
Config Hash
Cycle ID
Decision ID
Candidate ID
AI Invocation ID
Entry Intent ID
Entry Client Order ID
Entry Exchange Order ID
Entry Trade IDs
Position Group ID
Protection Client/Exchange IDs
Exit Trigger
Exit Intent ID
Exit Client Order ID
Exit Exchange Order ID
Exit Trade IDs
Final Reconciliation ID
```

Proof：

```text
NATURAL_SCHEDULER_TESTNET
```

只有该 Proof 自动校验通过，自动交易链路才算完成。

---

# 十七、Phase 12：Legacy 回归和 Cutover

## 17.1 Legacy 回归

确认五个 Gate 修改没有破坏：

- Legacy 普通 Paper；
- Legacy Testnet（若仍允许）；
- 原有 Gatekeeper；
- 原有 Scheduler Coordination；
- 原有前端只读页面；
- Migration 向后兼容。

## 17.2 Cutover

若当前已经 `v2_active`：

- 证明 Legacy Writer 不可达；
- 只有一个 V2 Writer；
- 重启后仍然唯一；
- Runtime API 显示 V2 Active；
- 回滚只关闭 Entry；
- 已有 V2 Position 仍由 V2 管理。

若尚未 Cutover：

- 不因此判定核心代码失败；
- 但不得声称正式生产已切换。

---

# 十八、Phase 13：量化策略优化前置审查

本 Phase 只判断是否适合开始优化，禁止调参、筛策略或改代码。

## 18.1 数据正确性

检查：

- 数据源和历史区间；
- 缺失 K 线；
- 重复 K 线；
- 异常价格；
- 时区；
- point-in-time 一致性；
- Funding；
- 手续费；
- 滑点；
- 盘口/下一根成交模型；
- Data Hash 和 Cutoff；
- 训练/OOS 隔离。

输出：

```text
READY
BLOCKED_DATA
BLOCKED_COST_MODEL
BLOCKED_LOOKAHEAD
```

## 18.2 回测与生产一致性

比较：

```text
Decision Pipeline
指标参数
闭合 K 线规则
Signal Reference
Price Drift
Sizing
Stop/TP
Exit
Fee/Funding
Position Concurrency
```

检查：

- Lookahead；
- 同 K 线收盘决策同价成交；
- 固定滑点偏乐观；
- Funding 缺失；
- 组合保证金缺失；
- BTC/ETH 相关性忽略。

## 18.3 统计证据

读取活跃候选：

```text
OOS Trade Count
Net Expectancy
Confidence Interval
Sharpe
Sharpe CI
MDD
Profit Factor
Regime Breakdown
Walk-forward
Bootstrap Type
Candidate Attempt Count
Final Holdout
```

检查：

- CI 是否跨 0；
- OOS 是否过少；
- Walk-forward 是否是真正 refit/lock/test；
- Bootstrap 是否处理序列相关；
- 是否记录全部候选和拒绝项；
- 是否有选择偏差；
- 是否有组合级回放；
- Manifest 是否绑定 Git/Data/Config/Strategy Hash。

## 18.4 优化准备状态

输出一个：

```text
STRATEGY_OPTIMIZATION_BLOCKED_EXECUTION
STRATEGY_OPTIMIZATION_BLOCKED_DATA
STRATEGY_OPTIMIZATION_BLOCKED_VALIDATION
STRATEGY_OPTIMIZATION_READY
```

只有全局结论为：

```text
ACCEPTED_READY_FOR_STRATEGY_OPTIMIZATION
```

才生成策略优化执行 Prompt。

---

# 十九、评分体系

评分不允许覆盖硬失败。

| 维度 | 权重 |
|---|---:|
| 生产入口与单写入者 | 12 |
| Exchange-First 事实链 | 14 |
| 自动 Exit/Protection | 12 |
| Recovery/Reconciliation | 12 |
| 数据与信号可观察性 | 8 |
| AI/Sampling 接入 | 6 |
| Runtime API/前端真实性 | 8 |
| 测试可信度与故障注入 | 10 |
| 运维/迁移/安全 | 8 |
| 量化研究准备度 | 10 |

总分 100。

## 19.1 P0 硬失败

任何以下情况，总结论不得高于 `REJECTED_P0`：

```text
无 Fill 创建 Managed Position
DB 不可用继续 Entry
对账不可用继续 Entry
硬 Exit 被入口 Gate 阻止
Cycle 不执行 Exit
本地在权威状态未知时 CLOSED
双 Writer
V2 Active 无 Scheduler
真实 Mainnet 风险
Protection 失败后继续 Entry
API 显示与真实状态相反
```

## 19.2 集成硬失败

任何以下情况，总结论不得高于 `REJECTED_INTEGRATION`：

```text
Cycle 不落库
Recovery 不执行
API 仍是内存字典
前端组件未挂载
AI/Sampling 无生产调用
真实脚本构造错误
Natural Observer 不可达
```

---

# 二十、最终报告格式

`GLOBAL_ACCEPTANCE_REPORT.md` 必须包含：

1. 基线；
2. 最终结论；
3. 一句话判断；
4. 五个 Gate 状态；
5. 生产调用图；
6. 测试结果，区分已有测试、独立探针、Strict Fake、Shadow、Contract、Natural、Frontend；
7. 数据库事实；
8. Exchange Evidence；
9. P0/P1/P2 Findings；
10. 量化策略优化准备度；
11. 评分；
12. 唯一下一步动作。

每项 Finding 必须包含：

```text
标题
文件/函数
最早断点
复现方法
实际结果
期望结果
影响
证据路径
最小修复边界
```

---

# 二十一、NEXT_ACTION_PROMPT 生成规则

生成：

```text
audit/global_acceptance/<RUN_ID>/NEXT_ACTION_PROMPT.md
```

## 21.1 `REJECTED_P0`

只生成修复最早 P0 断点的 Prompt。

禁止：

- 同时修 P1/P2；
- 优化策略；
- 前端美化；
- 开启真实 Testnet；
- 建 V3。

## 21.2 `REJECTED_INTEGRATION`

只修当前未通过 Gate。

要求：

- 从生产入口测试；
- 保留数据库事实；
- 完成后独立审查；
- 不跨 Gate。

## 21.3 `BLOCKED_REAL_ENV`

生成真实环境验收 Prompt：

```text
Shadow
→ Testnet Contract
→ Natural E2E
```

不再修改架构。

## 21.4 `ACCEPTED_ENGINEERING_NOT_STRATEGY_READY`

生成策略研究基础修复 Prompt，仅限：

```text
数据
成本模型
回测一致性
验证框架
Evidence
```

不调参数。

## 21.5 `ACCEPTED_READY_FOR_STRATEGY_OPTIMIZATION`

生成正式《量化策略优化 Master Prompt》，要求：

- 冻结执行链；
- 不修改订单、对账、API、前端；
- 锁定数据和验证协议；
- 建立不可触碰 Final Holdout；
- 运行 Baseline；
- 分策略家族研究；
- 记录全部候选尝试；
- Block Bootstrap；
- 真实 Walk-forward refit；
- Next-bar/VWAP 成交；
- Funding/手续费；
- 组合级回放；
- 选择偏差校正；
- 只有 CI 下界和多窗口稳健性达标才晋升；
- 优化结果只进入 Shadow/Testnet，不直接 Mainnet。

---

# 二十二、实际执行开头

将下面内容作为给 Codex/Claude Code 的开头：

```text
你正在执行一次只读、全局、生产入口级验收审计。

禁止修改任何生产代码、测试、配置、迁移、文档和前端文件。只允许在：
audit/global_acceptance/<RUN_ID>/
写入审计输出。

不要相信此前“所有 Gate 完成”“1100 tests passed”等摘要。你必须自行从一键启动、Scheduler、Task 注册和 Engine Activation 开始追踪正式调用链，并通过数据库事实、Stateful Strict Fake、Shadow、Testnet Contract 和 Natural Scheduler Testnet Evidence 验证。

固定旧审计基线：
9afa16681e1525897ab03b89ad1febc37c30d807

实际审计基线：
运行 git rev-parse HEAD 读取并锁定当前提交。

必须完整执行《AI-全局项目验收审查与下一阶段决策 Master Prompt》中的 Phase 0–13。任何真实网络测试被跳过都不得记为通过。

最终只允许输出：
REJECTED_P0
REJECTED_INTEGRATION
BLOCKED_REAL_ENV
ACCEPTED_ENGINEERING_NOT_STRATEGY_READY
ACCEPTED_READY_FOR_STRATEGY_OPTIMIZATION

审计结束后必须生成：
GLOBAL_ACCEPTANCE_REPORT.md
GLOBAL_ACCEPTANCE_MANIFEST.json
STRATEGY_READINESS.md
NEXT_ACTION_PROMPT.md

不要修复发现的问题。先完成审计，再由用户决定是否执行 NEXT_ACTION_PROMPT。
```

---

# 二十三、停止条件

遇到以下情况立即停止对应后续 Phase，但继续完成安全的静态报告：

```text
工作区不干净
Mainnet Endpoint
真实资金账户
无法归属仓位
补偿清理失败
两个 Writer
DB 状态损坏
Shadow 有写请求
Contract 不能归零
严重密钥泄漏
```

不得为了完成报告继续发送订单。

---

# 二十四、审计成功标准

本次审计成功不等于项目通过。

审计本身成功必须满足：

- 固定当前 SHA；
- 没有修改项目代码；
- 所有结论有原始证据；
- 区分 Fake、Shadow、Contract、Natural；
- 找到最早断点；
- 没有被测试数量误导；
- 给出唯一下一步；
- 没有在工程未通过前进行策略优化；
- 工程通过后才生成策略优化 Prompt。
