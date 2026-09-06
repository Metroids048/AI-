# 合并后项目诊断与验证报告

审查对象：AI Quant Research Platform；执行日期：2026-09-05 至 2026-09-06。本文是本次唯一诊断报告，不是修复或交易验收证明。

## 1. 一句话结论

**[已验证] 指定分支已合入本地 `main`，但本轮验证触发了合并前已存在的 P0 测试隔离缺陷：Canary 测试泄漏正式数据库地址，后续测试清理了正式库的业务表；另有测试覆盖正式调度状态。根因已确认，项目不能认定为通过验收。**

我执行了仓库要求的全量测试，但在执行前没有发现隔离缺口，也没有对正式数据库创建当时的完整快照。该测试运行造成了实际数据损失；对此承担执行责任。不能用测试绿灯、Git 干净或“缺陷原先存在”淡化影响。

## 2. 当前分析基线

| 项目 | 核对结果 |
|---|---|
| 本地路径 | `C:\Users\Windows11\Desktop\量化项目` |
| 远程仓库 | `https://github.com/Metroids048/AI-.git` |
| 合并前 | `main` / `6ed82b4d66ccb9227d8cf10d7d5e7b349f75d735`；跟踪文件无未提交修改 |
| 指定目标 | `origin/fix/ci-ruff-formatter-contract` / `a42ddcf43c666f5b711d34de73c94124397ba082` |
| 合并操作 | `git fetch origin`；`git merge --ff-only origin/fix/ci-ruff-formatter-contract`，成功，无冲突 |
| 合并后 | 本地 `main` 为 `a42ddcf`，比 `origin/main` 多 25 个已有提交；没有切换分支、创建分支、生成合并提交或推送 |
| 合并范围 | 42 files changed, 1532 insertions(+), 365 deletions(-) |
| 源码一致性 | `git diff --exit-code origin/fix/ci-ruff-formatter-contract HEAD` 返回 0 |
| 用户入口 | `一键启动.cmd:16` → `scripts/launch-paper-console.ps1`；浏览器地址 `/trading`，API 端口 8016，前端端口 5173 |
| Python 测试解释器 | `C:\Users\Windows11\.agent-reach-venv\Scripts\python.exe`，Python 3.12.3 / pytest 9.1.1 |
| CI 声明环境 | Ubuntu / Python 3.11，安装 `.[dev,ml]`；本轮不是该平台的完整等价运行 |
| 原实际运行版本 | [未知] 启动日志没有足以绑定进程与提交的版本证据；不能把当前磁盘 HEAD 说成曾经运行的进程版本 |
| 运行状态 | [已验证] 早期观测时 8016/5173 连接被拒绝；记录中的 supervisor 26140、worker 9156 不存在 |
| 时间证据 | 初次读取调度心跳为 2026-09-05 07:36:01 UTC；系统本次启动为同日 19:27:18 北京时间，晚于旧心跳 |
| 后续运行变化 | [已验证] 2026-09-06 01:23北京时间复查：项目进程自01:02启动，API health返回ok，Supervisor9040/worker16632；此启动不是本代理执行，发起者未知 |

已读取用户附件、项目约束、共享 runtime context、相关记忆与知识库、架构报告正文、当前入口和核心调用链。历史记忆存在过期信息，例如默认 Canary 权限说明前后不一致；本报告以当前代码和带时间戳的观测为准。

审查边界：合并后的源码、工具链、测试隔离、真实入口及 V2 执行主链。未读取凭据或 `.env` 内容；未启动真实下单入口；未声称当前 Binance 持仓已在线复核。

## 3. 核心业务与行为基线

平台目标是持续完成“想法规则化 → 历史验证 → 模拟执行 → 复盘 → 迭代”。AI 协助研究，确定性策略和风控决定交易行为。当前自动执行范围为 BTC/ETH Binance Simulation。

真正的执行成功证据必须是自然 Scheduler 决策产生的 Binance order ID、fill、保护、reduce-only exit 和最终交易所/本地对账；本地 SQLite 行、Mock 或 Canary 连通性结果不能证明策略有效。

正常行为基线有两部分：

- [事实] 合并前代码及冻结契约保护现有 V2 交易路径；合并后两套冻结哈希检查通过。
- [事实] 本轮清理发生前的早期只读观测中，正式库最近 BTC 周期为 `2026-09-05 07:35:17`，ETH 为 `07:35:47`，终态 `CANDLE_CLOSED`；最近决策为 `DUPLICATE_DECISION`。这是已有调度事实，不是本轮真实交易证明。

清理发生前读到的最新持久化对账时间为 `2026-09-05 07:35:11.126000 UTC`，状态 `HEALTHY`。解析嵌套数组后，历史快照中交易所持仓 **0**、本地持仓 **0**、开放订单 **0**、mismatches **0**。这些数字来自旧本地快照，**不等于当前在线交易所核验**。初查正式 V2 持仓、成交订单、incident 表均无记录，因此不能拿更早文档中的订单 ID填补本次证据。

必须保留的语义：交易所为成交真源；先授权、后提交、确认成交后投影；保护从真实成交价计算；未知订单通过原 client order ID 查询恢复；不能调风控/准入门槛制造信号；不得修改冻结执行链来修测试环境。

## 4. 当前实际执行链路

以下是读到的真实代码路径；早期服务未运行，后续仅核验健康响应和持久化状态，不能把静态链路记为完整动态验收。

| 步骤 | 文件与函数 | 输入、输出和失败边界 |
|---|---|---|
| 启动 | `一键启动.cmd:16`；`launch-paper-console.ps1:684` | CMD 显式指定 `v2_active`、`EnableNaturalTestnet`；launcher 准备 Python、数据库、API、Supervisor、前端 |
| Supervisor / worker | `scripts/run-local-paper-scheduler.py:298` `run_supervisor()`；`:70` `run_scheduler()` | 账户 writer/进程租约 → worker → bootstrap → RuntimeScheduler；启动异常记录 FATAL，不生成本地成交 |
| 调度协调 | `services/execution/scheduler.py:1485` `_run_coordinated_once()`；`:2004` `_default_v2_automated_trading_runner()` | Binance 时间、slot、lease、fencing、cycle claim；缺时间返回显式错误；重复 slot 拒绝重复执行 |
| 任务入口 | `services/execution/tasks.py:372` `run_v2_automated_trading_cycles()` | 把 provenance 交给 V2；旧 Paper 主链不是该入口的替代实现 |
| 周期组装 | `services/execution/v2_scheduler_entry.py:973`、`:1018`、`:1301` | 加载闭合 15m/1h/4h 数据、同周期 ConfigSnapshot、Production/Forward/Canary 权限 → CycleRequest；数据/配置错误单独记失败 |
| 交易所真相与恢复 | `services/automated_trading/application/cycle_service.py:1828` `run_automated_trading_cycle()` | 先 fetch_authoritative_snapshot，读本地状态、恢复已确认成交/退出缺口、对账；真相不可用时禁止新增风险 |
| 入场 gate | `entry_service.py:111` `evaluate_entry()`；`cycle_service.py:2521` | 检查 Shadow、kill switch、对账、重复持仓、成本、预算、有效期和价差；失败返回可见 reason |
| 持久化 intent / 提交 | `cycle_service.py:2648`、`:2705`；`entry_service.py:330` `execute_entry()` | 先存 intent，再提交；`binance_adapter.py:322` `submit_market_order()`；超时归类 UNKNOWN，不能盲目再下单 |
| 成交确认 | `binance_adapter.py:517` `fetch_fills()`；`entry_service.py:273` `position_projectable` | 必须有订单与实际 fill/恢复凭据；ACK 未成交不能创建已成交持仓 |
| 保护和投影 | `cycle_service.py:2779` 起；`protection_service.py:161`、`:229` | 通过成交门后，用 average_fill_price、filled_quantity 构建保护；保护失败走可见恢复/紧急退出状态；写回订单、fill、持仓、保护事实 |
| 退出与下一轮 | `cycle_service.py:1000`、`:1672`；`binance_adapter.py:707` | reduce-only 提交和成交确认 → 关闭/修正本地投影 → 下轮重新对账 |
| 用户监控 | `apps/api/routers/runtime.py:1442`；`runtime_state.py:168` | 汇总实际周期与状态；超过120秒的心跳应归为 stale，不能因为 JSON 文件存在就显示在线 |
| 研究验证 | `services/research/integrations/orchestrator.py:23` `run_pipeline()` | VectorBT 筛选 → Freqtrade → bias gate → native OOS；任一步缺证据即失败，成功也返回 promotion_authorized=false |

## 5. 正常基线、当前实现与近期修改差异

| 主链步骤 | 正常/原设计 | 本轮实际证据 | 分支变化与判断 |
|---|---|---|---|
| 交易提交与保护 | Exchange-first V2 | 冻结哈希通过；未在线运行 | 29 个生产 Python 改动文件 AST 全部相同；未发现提交路径迁移 |
| Python 格式契约 | 工具版本和冻结字节兼容 | 锁定 Ruff 后 Git 中808文件通过，本地4文件失败 | 固定 Ruff 0.15.20并排除冻结文件；4文件混合换行在合并前已存在 |
| Linux 类型检查 | 保留 Windows API 且可在 CI 检查 | mypy1.20.2 Linux对照验证了3模块例外 | 取消例外产生9个Windows API属性错误和2个unused-ignore；恢复后0错误 |
| Freqtrade 错误测试 | 应到达被测错误分支 | 2个修改测试文件合计聚焦10 passed | 明确隔离 VectorBT 可用性和 executable；未删除原失败断言 |
| Windows PID 测试 | 不调用 os.kill 破坏进程 | 局部 OS 代理保留断言 | 避免全局 os.name 污染 pathlib，属于测试隔离修正 |
| 数据库测试隔离 | 每用例只清理测试库 | 本轮正式库表被清空 | P0根因文件与合并前相同；分支未解决此独立缺陷 |
| 调度状态测试隔离 | 仅写临时状态 | 正式状态被测试 PID覆盖 | 同样为未修改的存量缺陷 |
| 前端依赖审计 | high级门禁通过 | 6项漏洞，其中4 high | package.json/lock文件不在合并差异，属存量审计失败 |

39个改动Python文件中37个AST完全相同，只有两个测试文件的AST不同。AST等价不覆盖依赖升级效果，也不保证运行环境或本地数据安全。策略计算逻辑没有语义变动，本轮未重跑候选竞赛或宣称任何新收益指标。

## 6. 根因结论

### P0-DB-001：全量测试会清理正式数据库（BLOCKER）

- **用户现象**：[已验证] 清理发生前已观测到的正式 V2周期、决策和对账数据在测试后消失；`strategies`、`paper_runs`、`v2_execution_cycles`、`v2_execution_events`、`v2_execution_decisions`、`v2_reconciliation_snapshots`、`market_extras` 的行数均变为0；运行控制表也为空。
- **入口**：在项目根目录执行 `python -m pytest -q`。
- **触发**：`tests/scripts/test_run_testnet_canary_acceptance.py:26` 的测试调用 `module.main()`，只 mock 下单函数，未恢复进程环境。
- **错误状态**：`scripts/run_testnet_canary_acceptance.py:32` 将 `POSTGRES_URL` 设置为正式 `.local_paper_console.db`。
- **直接原因**：后续 `tests/services/test_database_schema.py:19` 调用 `reset_database_caches()`；`services/database.py:40` 的默认 Engine 以 `None` 作缓存键，重建时采用被污染的环境地址。
- **破坏边界**：下一测试 setup 进入 `tests/conftest.py:88`，获得正式库连接；第93/97行对映射表执行无条件 DELETE，没有断言实际连接的规范化路径等于 `TEST_DB_PATH`。
- **深层根因**：会话初始化一次测试环境，被误当作每个用例生命周期中的持续隔离；可变进程环境、默认连接缓存和无路径检查的清表操作组合形成破坏性越界。
- **安全复现**：按顺序运行 Canary命令测试、`test_create_local_runtime_schema_includes_relational_and_runtime_tables`、`test_adopt_complete_legacy_sqlite_schema_for_alembic_without_losing_data`；进程级 audit hook 在 sqlite3.connect 打开正式库之前拒绝。结果 **2 passed / 1 error**，第三项 setup 报 `DIAGNOSTIC_GUARD_BLOCKED_REAL_DATABASE_CONNECT`。这次复现未打开正式库。
- **为何此前修改无效**：本分支修的是格式、平台类型、Freqtrade和OS测试隔离，没有处理 Canary 命令环境泄漏或清表路径校验；所有业务断言仍可在错误数据库中通过。
- **历史归属**：[已验证] 根因相关文件相对 `6ed82b4` 逐字节相同。原全量运行没有 SQL追踪器，因此不声称掌握首次破坏性DELETE的精确用例时刻；上述最小复现已验证其机制。
- **置信度**：高。修复后应共同消除测试跨用例切库、正式库清表、清理到错误库仍绿灯的现象。

### P0-STATE-002：测试覆盖正式调度状态（BLOCKER）

- `tests/services/test_natural_testnet_authority_bridge.py:121`、`:206` 在 finally 中调用 `await scheduler.stop()`。
- `scheduler.py:1222 → _publish_external_state():1835 → runtime_state.py:160` 写入状态；未隔离 `LOCAL_SCHEDULER_STATE_PATH` 时目标为正式 `logs/scheduler-state.json`。
- [已验证] 本轮状态从原心跳 `07:36:01 UTC`、worker9156改为测试PID23084和测试时间；不能再把此文件当作原运行事实。
- [已验证] 独立复现用内存拦截器捕获两次正式路径写入请求，两个测试仍 **2 passed**；实际写入被阻断。相关文件与合并前相同。
- 根因：测试Mock覆盖部分方法，未覆盖 stop/finally 的真实状态写入。置信度高；修复应让所有生命周期路径只写临时状态，并断言正式状态字节不变。

### ENV-003：工具和工作树格式不一致

- PATH Ruff0.16.4、mypy2.3.1与分支要求不一致；隔离工具使用Ruff0.15.20、mypy1.20.2。
- 4个文件为Git LF、本地mixed：`scripts/run_testnet_canary_acceptance.py`、`services/automated_trading/application/operator_profile.py`、`tests/audit/test_v2_runtime_requirements.py`、`tests/scripts/test_run_testnet_canary_acceptance.py`。
- [已验证] 各文件 `BASE_GIT == HEAD_GIT == WORKTREE.replace(CRLF, LF)`，Git版本格式检查均0，实际文件检查1。`core.autocrlf=true`，无仓库 `.gitattributes`；不能把具体混合换行的最初编辑工具当已知。
- 这是本地存量，不是新业务回归；本轮未擅自格式化冻结或未修改文件。

## 7. 历史修改与误判分析

| 提交 | 改动意图/实际结果 | 证据边界 |
|---|---|---|
| `272fad6`、`cb79425` | 固定Ruff并按该版本格式化 | Python主体最终为语法等价调整 |
| `3d2c0cb`、`3b817e9` | 恢复误触冻结的字节，Windows类型差异转为有限配置例外 | 当前两套冻结和独立Linux类型对照通过 |
| `98633d3`、`364b29d`、`65d7a13` | 逐步修正formatter排除项，最终显式保留archives排除并添加冻结文件 | 最终命令已按锁定版本实际验证 |
| `27248bf`、`df0ebb7`、`1e17270` | 隔离依赖安装状态、OS全局变量对单测的影响 | 未改变Production实现、断言阈值或调用真实服务的权限 |
| `a95d6dc`、`a42ddcf` | 曾临时使用首失败输出，最终移除`-x`，运行整个既有非integration集合 | `not integration`在原CI中就存在，没有新增范围缩减 |

没有读取远端每次CI失败日志，因此上述是Git可支持的修改链，不是逐次远端故障复现。它们解决的工具问题并不覆盖“测试是否写入正式资源”。本轮最重要的反例就是 **2078项断言通过，同时正式业务表遭清理**。

## 8. 已执行验证

所有命令在项目根目录运行。工具输出保存在 `.local/test-runtime/merge-review-*`；审计副作用与恢复资料另存 `.local/merge-review-incident-20260905/`。这些是运行输出和恢复副本，未提交。

| 命令/程序 | 结果 | 证明范围 |
|---|---|---|
| `pre-commit install`；核对`.git/hooks/pre-commit` | PASS；commit/push hooks已安装 | 没有提交，故不存在本轮提交钩子成功证明 |
| `git merge --ff-only origin/fix/ci-ruff-formatter-contract` | 0；快进成功 | 指定源码融合 |
| 锁定工具 `python -m ruff check .` | 0；`All checks passed!` | lint |
| 锁定工具 `python -m mypy` | 0；`Success: no issues found in 315 source files` | 配置指定范围；有2条untyped提示 |
| CI的Ruff format命令，实际工作树 | 1；`4 files would be reformatted, 804 files already formatted` | 本地换行存量失败 |
| 同命令，临时目录中的HEAD Git Python字节 | 0；`808 files already formatted` | 提交字节符合格式；没有切分支或改工作树 |
| `python -m pytest -q --junit-xml=.local/test-runtime/merge-review-pytest.xml` | 0；`2078 passed, 7 skipped, 17 warnings in 251.35s (0:04:11)` | 断言绿灯，**资源隔离失败，不构成安全验收** |
| 两个实际改动测试文件，隔离工具解释器 | 0；`10 passed in 6.07s` | 两处测试修改；不是完整新依赖环境验收 |
| `python -m pytest -q -m 'not integration' ...` | 因发现资源污染被主动终止，没有完整汇总 | 不得记为CI集合通过 |
| `refresh_current_state.py --junit-xml ... --check` | 1；verification block is stale | 全量7skip与文档非integration5skip口径不同；没有将此定为新代码缺陷，也未手改数字 |
| `sync_skill_copies.py --check` | 0；all mirrors match | 技能同步 |
| `verify_current_state_evidence.py` | 0；evidence matches active manifest | manifest引用一致 |
| `verify_automated_trading_contract.py --verify-baseline --verify-head --verify-ledger` | 0；`ENGINEERING_FREEZE=PASS; NATURAL_TESTNET_EXECUTION=PENDING` | 工程冻结，不是自然订单验收 |
| `verify_v2_transaction_contract.py --verify-baseline --verify-head` | 0 | 交易冻结 |
| `npm --workspace frontend/admin run test` | 0；21 Test Files、118 Tests passed | 前端单元测试 |
| `npm run admin:build` | 0；built in543ms | 构建；存在大于500kB chunk警告 |
| `npm audit --audit-level=high` | 1；`6 vulnerabilities (2 moderate, 4 high)` | 存量依赖门禁失败，未自动升级 |
| 声明的dev/ml依赖集合 `pip_audit -r ...` | 未完成；依赖解析报`No matching distribution found for websockets<16.0,>=12.0` | 环境/索引解析阻塞，不能宣称Python安全审计通过 |
| `compose_validate.py --require-docker` | BLOCKED；docker not found on PATH | 未验证Compose运行 |
| `git diff --check 6ed82b4 HEAD` | 0 | Git差异空白检查 |

Python warnings包括Starlette/httpx、Pydantic class Config和Alembic path_separator弃用提示。当前PATH环境还缺telethon、pytest-cov，cryptography为48.0.1而新声明要求50.x；隔离工具环境安装了50.0.1，但正式启动环境未被更新或验收。

前端业务代码未变，本轮未启动交易系统或浏览器操作；**关于运行中的页面功能，未经浏览器验证**。真实Binance下单、自然退出、重启恢复和最新交易所持仓均未验证。全量7个skip包含实时Testnet、自然E2E、公共Binance、LLM和数据要求未满足的用例，不能当作已覆盖。

## 9. 待验证事项、数据影响与恢复边界

本轮直接观察到正式库的业务表被清空。早期数据库和状态观测发生于全量测试已启动、尚未清理正式表的阶段，并不是一次完整的起跑前快照；没有该快照是本次执行的遗漏。文件大小仍为3,231,449,088字节不能证明数据仍可正常查询：788,545/788,928个页已在freelist中。

已保存受影响数据库原始副本：`.local/merge-review-incident-20260905/affected-paper-console.db`，SHA256为 `82a905d554bf132767681e64ade3ceaa3c2fe468863e04b8b085304f94753c22`。这是**事故后保全副本，不是事故前备份**。调度状态的事故后副本及hash也在同目录。

恢复检查发现free pages仍含数据，secure_delete默认0。已使用SQLite官方3.53.4工具尝试提取；下载包SHA3-256与官方一致。第一种CLI管道导入遇到损坏记录的SQL解析错误，不能把0字节候选库当恢复成功；改为先将原始提取输出保存为`recovered-pages.sql`，保留后续核验机会。提取退出码0，输出5,387,744,738字节；提取成功不等于原表已恢复。

SQLite官方明确说明恢复可能重新引入以前删除的数据、丢失/改变字段或错归表，必须验证后才能恢复使用：[SQLite恢复说明](https://www.sqlite.org/recovery.html)。本轮不能根据“提取到了记录”认定完整回滚成功。

### 本轮数据保全的最终结果

独立候选文件为 `.local/merge-review-incident-20260905/salvaged-records-unverified.db`（3,588,636,672字节）。Python导入执行了6,960,874条SQL语句，拒绝46条（44条UTF-8解码错误、2条SQL操作错误），原语句保存在`rejected-recovery-statements.bin`，没有通过替换字段内容来伪造可用行。

候选库有70张表，非空的只有`alembic_version`和`lost_and_found`；后者有**6,960,606条待归属片段**。因此这是残留数据保全成果，**不是已恢复的正式业务库**。`PRAGMA quick_check`为`ok`只证明该候选文件可正常读取，不证明业务内容完整或真实。

进一步只读结构筛选得到：

- 51,324条符合V2周期字段结构的片段，时间范围2026-07-31至2026-09-05。最新4条的symbol、timeframe、started_at、completed_at和terminal，与清理前读到的4条正式周期观测完全一致。
- 41,605条符合对账字段结构的片段，最新时间为`2026-09-05 07:35:11.126000`，与早期对账观测一致。
- 这两组是**按字段结构筛选的片段数**，不是经过完整归属、去重、外键/权限核验后的正式表行数；不能据此声称恢复比例或据其启动自动交易。

结构检查结果保存在`salvage-shape-check.json`，导入统计在`salvage-import-result.json`，事故后原始副本hash在`preserved-evidence-hashes.json`。原始副本、提取SQL、候选库和拒绝记录均保留；正式数据库没有被候选库替换，正式调度状态没有被人为重造。

### 后续检测到的运行变化

2026-09-06 01:23北京时间的只读复查发现，项目进程已在01:02重新启动；本代理没有发起此启动，不能推断发起者。API `/health` 返回`ok`。调度状态为`ACTIVE / BINANCE_TESTNET / TESTNET_CANARY`，`entry_authorized=true`、`TRADING_READY`、`reconciliation_healthy=true`，存在新的Supervisor/worker和有效ConfigSnapshot。

该次数据库观测已有33个V2周期、32条决策，周期范围为`2026-09-05 17:05:04..17:23:20 UTC`；订单、fill、managed position记录均为0。前一次相邻读取还发现6条strategy、1条runtime control。数字是观测快照，会随运行变化；这批新数据**不表示历史数据已经恢复**。

因此正式库hash已不同于事故后保全副本，不能再把“与副本字节一致”当作当前条件。保全副本仍保留原事故状态；本代理没有用恢复候选覆盖正式库，也没有停止新启动的进程。后续恢复必须保留这批新运行数据，不能简单整库回滚。在线交易所持仓及自然订单闭环仍未独立复核。

现有较早完整主库备份最近核到2026-08-17/18；另有2026-09-03的acceptance专用库，不能等同正式库。历史研究库保留了4,842,648根OHLCV（到2026-07-29），也不能替代当前配置和实时账本。没有用这些旧备份覆盖正式库。

会改变下一步判断的最少证据：

1. 恢复记录的表归属、主外键、最新时间范围、与事故前已经观测到的周期/配置事实一致性。
2. 若存在未扫描到的2026-09-05测试前正式库备份或另一个真实运行设备，其文件位置和时间点。
3. 资源隔离修复后，对故意污染POSTGRES_URL、清缓存、进程退出/finally的拒绝写入证据。
4. 正式运行恢复后，新自然周期、最新交易所只读快照和本地投影一致性。不能从当前被清空的本地库推断交易所已空仓。

## 10. 下一阶段诊断交接

**唯一优先目标：恢复可信的本地运行资料，并使测试无法触及正式数据库和正式调度状态。** 不开展策略优化、风险参数调整或执行链重构。

| 项目 | 边界 |
|---|---|
| 已确认问题 | P0-DB-001、P0-STATE-002 |
| 下一阶段拟允许范围（本轮未实施） | `tests/conftest.py`的逐用例资源隔离与清理前实际数据库路径硬校验；Canary测试的环境恢复；natural authority测试的状态路径隔离；必要的定向回归 |
| 禁止范围 | 冻结Binance适配器、风控/止损止盈/杠杆数值、Production/Canary权限语义、交易数据真源；不得把生产数据库路径改成测试路径来掩盖测试污染 |
| 禁止绕过 | 跳过Canary/schema测试；删除清表fixture；只mock连接而不验证路径拒绝；把2078 passed继续当最终验收 |
| 修复验收 | 三用例污染序列不能连接真实库；故意传入正式库地址时清理必须失败；正式DB与状态文件在测试前后字节不变；每次运行写入独立临时目录 |
| 数据验收 | 恢复候选独立核验，不允许用过期备份直接覆盖；配置、ownership、订单/fill、持仓/保护和对账关系可核对后才讨论恢复启动 |
| 必须回归 | 两个资源污染最小复现；两个分支改动测试；随后才是有路径硬保护的完整pytest、CI集合和现有冻结校验 |
| 停止条件 | 任何测试请求正式资源写入立即阻断；恢复数据无法归属/无法判断完整性时不替换正式库、不启动自动交易 |

附带存量问题：4个混合换行文件、前端6项依赖漏洞、PATH环境与依赖声明差异、Docker缺失。它们与P0分开处理，不能转移事故处置的优先级。

## 11. 最终状态与交付自查

**诊断完成，可以进入方案设计。** 源码合并成功；安全测试验收失败；正式库数据恢复尚未达到可运行验收。没有在本轮额外修改业务代码、提交或推送。

- [已验证] 真实启动入口、调用者、返回/异常分支、持久化与外部边界已追踪。
- [已验证] 远端目标、本地HEAD和未提交状态分别核对，Git字节和实际工作树差异分别验证。
- [已验证] 合并的关键配置与两个语义变动测试已重新读取；生产Python改动AST核对，冻结文件hash核对。
- [已验证] 独立Reviewer将初步“无新增业务缺陷”更新为 **P0 BLOCKER**，安全复现已提供新证据。
- [已验证] 正式库和状态被测试影响已披露；事故后副本已保全，没有伪造恢复后的运行状态。
- [已验证] 恢复SQL已提取并导入独立待核验候选；最近周期/对账片段与清理前观测一致；未宣称正式表已恢复。
- [未验证] 当前自然交易闭环、浏览器运行行为、最终恢复完整性；没有以单测替代这些证据。

```text
[验证] ruff check .   -> All checks passed!
[验证] mypy            -> Success: no issues found in 315 source files
[验证] pytest -q       -> 2078 passed, 7 skipped, 17 warnings in 251.35s (0:04:11)
[验证] git diff --stat -> 合并范围：42 files changed, 1532 insertions(+), 365 deletions(-)；另有本诊断报告
[基线对比]             -> 未发现新增生产Python语义变化；P0隔离缺陷在合并前已存在，但本轮运行已触发正式数据清理，安全验收FAIL。
```

上述pytest汇总仅忠实记录断言结果，**不表示执行过程安全或项目已通过验收**。
