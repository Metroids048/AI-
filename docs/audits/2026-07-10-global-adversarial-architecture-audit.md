# 全局对抗性测试与架构合规审查报告

审查日期：2026-07-10
审查基线：`ec60068` 加当前工作区未提交变更；未修改产品代码或既有测试。

## 执行摘要

本次完成了文档/历史对账、Python 与前端回归、决策和执行层对抗性检查、Testnet 最小开平仓实测，以及 11 条前端路由的浏览器巡检。Python 回归为 `196 passed, 1 skipped`，目标决策/风控测试为 `38 passed`，前端 Vitest 为 `12 passed`，Mypy 与前端生产构建通过。

总体风险：**Critical**。当前工作区无法在干净、已迁移的数据库上完成 API 启动；另外，开发级 CCXT 请求日志会暴露认证请求头。任何实盘阶段均应被下列 Critical 项阻塞。

## 已验证通过

| 模块 | 实测证据 | 结论 |
|---|---|---|
| 回归基线 | `py -3 -m pytest -q` | `196 passed, 1 skipped`；保留一个 FastAPI/TestClient 弃用警告。 |
| 前端单测/构建 | Vitest、`npm --workspace frontend/admin run build` | `12 passed`，Vite 生产构建成功。 |
| 风控与决策故障注入 | 决策、Gatekeeper、LLM、通知、运行时目标测试 | `38 passed`，覆盖陈旧数据拒单、预算耗尽 fail-closed、LLM 异常、通知和 Kill Switch 等既有场景。 |
| 回测指标和保护价边界 | `.local/audit/adversarial_pure_checks.py` | Sharpe 总体标准差口径为 `8.4852813742`；Profit Factor `1.0`；最大回撤 `0.090909...`；零止损价被拒绝。 |
| 空 RAG | `collect_rag_snippets(...空输入...)` | 返回空列表，不阻塞调用方。 |
| Binance Testnet | 预检 + `scripts/testnet_open_close_once.py` | 强制 `LIVE_TRADING_ENABLED=false`、`BINANCE_USE_TESTNET=true` 后，以不超过 120 USDT 的 BTC/USDT 市场开仓和 reduce-only 平仓完成，并在交易所 recent-orders 中对账。详细订单数据未写入本报告。 |
| 前端布局骨架 | Playwright 11 路由、390px 交易台 | 导航框架存在；窄屏交易台未发现水平溢出。 |

## 问题清单

| 编号 | 所属阶段/模块 | 严重程度 | 问题描述 | 复现步骤 | 证据 | 是否与架构/需求冲突 | 建议修复方向 |
|---|---|---|---|---|---|---|---|
| AUD-001 | API / 自动交易启动 | Critical | 迁移后的空数据库启动 API 时，`bootstrap_operator_experience_strategy()` 写入 `paper_status="disabled"`，但 `RunStatus` 不接受该值，API 生命周期直接退出。 | 对隔离 SQLite 执行 `py -3 -m alembic upgrade head`，再以 `RUNTIME_SCHEDULER_AUTOSTART=false` 启动 `uvicorn apps.api.main:app`。 | 实际栈：`ValueError: 'disabled' is not a valid RunStatus`，来源为 `services/execution/bootstrap.py` -> `repository._strategy_from_orm`。 | 是。状态矩阵将前端/API 标为已实现或部分实现，但干净运行态无法启动。 | 统一持久化状态值与 `RunStatus` 枚举；新增“空数据库迁移后 API lifespan 启动”回归测试。阻塞进入 Paper/Testnet 自动运行。 |
| AUD-002 | 凭据安全 / CCXT | Critical | 开发环境 CCXT DEBUG 请求日志会输出认证请求头和请求签名。 | 使用现有 Testnet 凭据执行只读预检或开平仓脚本，并捕获标准日志。 | 本次实测日志包含 API 认证头与签名；原始日志已立即删除，未在报告中保留。`shared/logging.py` 在 development 使用 DEBUG。 | 是。凭据管理和 API Key 最小暴露原则被破坏。 | 默认禁用 CCXT wire logging；对 headers/query 签名做强制脱敏；增加日志红队测试。**轮换本次 Testnet API Key。** |
| AUD-003 | 交易所 API Key 权限 | Critical | 风控设计要求启动期拒绝带提现权限的 Key，但全仓未实现权限查询或拒绝路径。 | 运行全仓权限关键字搜索，并对 `probe_testnet_account`/gateway 启动路径检查其返回字段。 | `docs/architecture/risk-control-and-safeguards-plan.md` 要求该检查；网关只查询余额、持仓、订单，未查询/暴露权限状态。 | 是，直接偏离已裁决的硬性要求。 | 在任何交易网关初始化前查询权限；检测到提现权限即拒绝启动并产生风险事件/可见状态；加入 Mock 交易所测试。阻塞实盘。 |
| AUD-004 | 交易所下单幂等性 | High | `ExecutionOrderRequest.idempotency_key` 未映射为 Binance client order id；超时后的重试可提交第二笔外部订单。 | 运行 `.local/audit/gateway_idempotency_check.py`，以相同 key 调用网关两次。 | 假客户端记录到两次 `create_order`，参数均无 `clientOrderId`/`newClientOrderId`。 | 是，违反 7x24 模糊提交必须查询或幂等确认的要求。 | 由稳定且长度受限的 idempotency key 生成 Binance client order id；超时先按该 id 查询；增加重复提交/超时测试。阻塞自动 Testnet 镜像。 |
| AUD-005 | 下单最小名义金额 | High | 小于最小名义金额的请求不会被预先拒绝，而会被网关放大到最小 50 USDT，可能超出策略或用户意图。 | 运行 `.local/audit/adversarial_pure_checks.py`，提交 10 USDT 请求、价格 100。 | 请求 10 USDT，`_resolve_gateway_quantity()` 输出 50 USDT。 | 是，审查要求为提前拦截，不应静默放大下单量。 | 在 Gatekeeper 中拒绝低于当前交易所 min_notional 的请求，或要求显式 operator opt-in 后再调整；记录原请求与调整原因。 |
| AUD-006 | 本地启动脚本 / 迁移 | High | `start_paper_console.ps1` 宣称初始化本地数据库，但其 API 启动脚本只重置连接缓存，不运行 Alembic；空 SQLite 首次启动先因缺表失败。 | 新建隔离 SQLite 后直接启动 API。 | 实际错误：`sqlite3.OperationalError: no such table: risk_profiles`；`scripts/run-api-local.ps1` 无迁移调用。 | 是，24x7/本地运行设计要求可靠恢复。 | 启动脚本显式执行并检查 `alembic upgrade head`，或安全地在首次启动创建 schema；增加全新数据库启动 smoke。 |
| AUD-007 | 前端故障状态 | Medium | 后端不可达时，交易台长期显示“正在加载交易台数据...”，验证页显示裸露的 `Failed to fetch`，同时浏览器产生大量 API/WS connection-refused 错误。 | 启动 Vite、令 API 因 AUD-001 退出，使用 Playwright 访问 11 条路由。 | 11 条路由均发起 API 请求；Playwright 记录 connection-refused，交易台未进入可理解错误状态。 | 是，PRD 的可用性/错误状态要求未满足。 | 统一请求失败状态、停止无意义 WS 重连、显示恢复操作与后端不可用提示；为 API 断开添加浏览器测试。 |
| AUD-008 | 工程质量门禁 | Low | 全仓 Ruff 未通过。 | `py -3 -m ruff check .`。 | `scripts/_run_auto_cycles_verify.py` 有一个未使用 import 和两项 import 排序问题。 | 否，但 CI/质量门禁不完整。 | 修复脚本格式问题，并把全仓 Ruff 作为变更前合并门禁。 |

## 架构漂移对照

| 文档声明 | 代码实测 | 结论 |
|---|---|---|
| A/B/C/D/E 五级数据源按阶段启用 | A 级仓储/数据测试存在；E 级本地资产与空 RAG 路径可运行；B/C/D 真实凭据和长连接未在本机验证。 | A、E 部分一致；B/C/D 为部分落地。 |
| 策略必须经验证后才进入 Paper/Execution | Gatekeeper 目标测试通过，拒绝缺止损、缺验证、陈旧数据、风险事件和 veto。 | 一致（静态/单测层）。 |
| Validation 以 Sharpe、PF、DD 等门槛准入 | 独立指标复算通过；Sharpe 使用总体标准差，文档未固定样本/总体口径。 | 部分一致；指标口径应在方法论文档明确。 |
| 执行层风险优先、无止损拒单、Testnet 隔离 | 无止损与零止损价路径被拒绝；Testnet 开平仓实测成功；但低名义金额自动放大、外部幂等缺失。 | 部分漂移，AUD-004/005 阻塞自动化执行。 |
| LLM 只做否决且失败关闭 | 目标测试覆盖预算耗尽/失败关闭；空 RAG 不阻塞。真实三 Provider 全失效和生产调用预算未验证。 | 单测层一致，生产条件无法验证。 |
| Review 为一级闭环 | Review/通知目标测试通过；由于 API 启动失败，真实运行态回写未验证。 | 部分落地。 |
| 状态矩阵称前端控制台“partial”且已有 smoke | 当前已迁移空库 API 启动失败，11 条路由无法取得正常 API 数据。 | 文档高估当前可运行状态，需更新。 |

## 无法验证

- Docker 不在 PATH：Compose、Celery worker/beat、Redis/Postgres 故障恢复、容器级 7x24 压测无法验证。
- API 启动崩溃：正常数据态前端、登录过期、真实轮询/实时推送和 P1 可解释性面板数据绑定无法验证。
- 缺少受控 LLM Provider 故障环境：三 Provider 同时不可用、真实预算耗尽后的告警投递只能以现有 Mock 测试为证。
- Binance 不提供本项目已接入的提现权限自检，因此该项是已验证缺失，不能被 Testnet 成功开平仓替代。
- 用户尚未提供已发现的前端问题清单，未执行其逐条专项复现。

## 按严重度排序的行动清单

1. **Critical，阻塞 Paper/Testnet 自动运行：** 修复 `disabled` 状态与 `RunStatus` 的契约冲突，并用干净迁移库验证 API lifespan。
2. **Critical，阻塞任何继续使用当前 Key：** 关闭/脱敏 CCXT 请求日志并轮换 Testnet API Key。
3. **Critical，阻塞实盘：** 实现提现权限 API Key 自检及拒绝启动。
4. **High，阻塞自动 Testnet/实盘：** 实现交易所 client order id 幂等确认和超时查询。
5. **High，阻塞按策略意图执行：** 改为拒绝低于 min_notional 的订单，禁止静默放大。
6. **High：** 让本地启动路径先迁移数据库；补全从零启动 smoke。
7. **Medium：** 完成 API 断开时的前端错误/重试体验并在 API 恢复后重新跑全路由巡检。
8. **Low：** 修复 Ruff 问题并更新状态矩阵的运行态证据和日期。

## 修复验证附录（2026-07-10）

本附录记录审查结论后的最小修复闭环。原问题表保留发现时证据，不应据此误读为当前状态。

| 原编号 / 新发现 | 状态 | 修复与最新证据 | 剩余限制 |
|---|---|---|---|
| AUD-001 / AUD-006 | 已修复 | `RunStatus.NOT_STARTED` 替代无效的 `disabled`；`run-api-local.ps1` 先执行 Alembic，再为 SQLite 创建本地运行时表。全新隔离 SQLite 经迁移、schema 初始化与 FastAPI lifespan 后，`GET /health` 返回 `200`。回归：`tests/services/test_paper_bootstrap.py`、`tests/services/test_database_schema.py`。 | TimescaleDB/Docker 初始化路径仍受本机无 Docker 限制，未作为通过项宣称。 |
| AUD-002 | 已缓解 | `shared/logging.py` 将 `ccxt` / `urllib3` 外部库日志最低限制为 `WARNING`，并有 `tests/contracts/test_logging.py` 覆盖。 | 本次审查期间使用过的 Testnet Key 仍应由操作方轮换；报告和工件未记录凭据、签名或余额。 |
| AUD-003 | 已修复（无凭据占位路径除外） | 真实 Binance 客户端初始化读取 `canWithdraw`，检测到提现权限即拒绝；无凭据的占位网关不执行远程权限查询且不可交易。回归：`tests/services/test_binance_gateway.py`。 | 交易所 API 是否返回该字段仍依赖其实际行为；无 Key 不能替代有 Key 的启动预检。 |
| AUD-004 / AUD-005 | 已修复 | 幂等 key 映射为长度受限的 Binance `newClientOrderId`，超时按该 ID 查询；低于 `min_notional` 的开仓请求改为拒绝，禁止静默放大。回归覆盖重复 ID、超时恢复和最小名义金额拒绝。 | 未在本轮再次提交 Testnet 订单；原审查的限额内开平仓/对账证据仍有效。 |
| AUD-007 | 已修复并浏览器复验 | HTTP 网络异常和代理 5xx 都统一显示“服务暂时不可用，请稍后重试”；错误进入状态后停止轮询/WS 重连。受控 `8000 + 5173` 实例中，正常交易台无 console error；断开 API 后提示出现，额外等待 6 秒无新增 API 请求。AutoSettings 空值、Ops 刷新 POST 契约和同交易所多网关 key 告警亦已修复。 | 测试使用空 SQLite 和禁用凭据/调度器；不代表真实登录过期、真实推送或第三方 Provider 已验证。 |
| AUD-008 | 已修复 | `py -3 -m ruff check .` 最新输出为 `All checks passed!`。 | 无。 |

### 最新验证汇总

- `py -3 -m pytest -q`：`205 passed, 1 skipped`；保留一个第三方 Starlette/TestClient 弃用警告。
- `py -3 -m ruff check .`：通过。
- `npm --workspace frontend/admin run test`：`8` 个文件、`17` 项测试通过。
- `npm --workspace frontend/admin run build`：通过。
- `git diff --check`：通过（仅 Windows 行尾转换提示）。
- `py -3 -m mypy .`：**未通过**，`23` 个文件共 `68` 项类型错误；主要是测试构造器仍传字符串而非枚举、可空 ID 和 Protocol fixture。该类型门禁需单列整改，不得写为通过。
