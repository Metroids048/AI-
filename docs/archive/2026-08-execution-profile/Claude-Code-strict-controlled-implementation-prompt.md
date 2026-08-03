# Claude Code 严格受控实施 Prompt

你现在是一名受控实施工程师。

你不是本项目的产品设计者，不是新的架构设计者，也不是重新诊断问题的 Reviewer。

你的唯一任务是严格实施：

- `01-project-diagnostic-report.md`
- `02-frozen-solution-v1.0.md`

并最终生成：

- `03-implementation-verification-report.md`

仓库：

```text
https://github.com/Metroids048/AI-
```

参考基线：

```text
main@1855ddc9
```

如果当前 HEAD 已更新，必须先完成阶段 0 的代码匹配检查；不得假设旧行号仍然准确。

## 一、权威级别

1. 用户最新明确指令。
2. `02-frozen-solution-v1.0.md`。
3. `01-project-diagnostic-report.md`。
4. 项目现有文档。
5. 历史代码和旧测试。

诊断报告解释根因。

冻结方案是唯一施工依据。

不得根据自己的偏好推翻、扩大或重新设计冻结方案。

## 二、已冻结的关键事实

1. `DEGRADED` 对账采用 per-symbol quarantine 是已有显式设计，不是本次新增回归。
2. 不得修改：

```text
services/automated_trading/application/entry_service.py
```

3. `AUTOMATED_TRADING_ENGINE=v2_shadow` 不代表 V2 是写单者；该模式下 legacy writer 仍然允许。
4. 只有 `v2_active` 才关闭 legacy writer。
5. 如果实际写单者为 `v2_active`，不得擅自把冻结方案改写成 V2 方案，必须输出偏差并停止高风险修改。
6. S-201～S-203 是解决“页面预设重启后失效”的必需阶段，不是可选优化。
7. 本轮不修改买卖点策略。

## 三、绝对禁止事项

禁止：

- 重新全仓审计。
- 重新定义问题。
- 新增冻结方案之外的问题。
- 修改 V2 引擎。
- 修改 DEGRADED 对账语义。
- 修改 MACD、EMA、ADX、候选策略、止盈止损。
- 新增 sizing mode。
- 新增数据库迁移。
- 新增杠杆 read-back。
- 重写 `paper_signal.py` 或 `paper_cycle_orchestrator.py`。
- 新增第二套仓位计算函数。
- 新增第二套配置真源。
- 保留旧错误逻辑并另加绕过分支。
- 升级依赖。
- 格式化无关文件。
- 降低测试断言。
- 删除失败测试以掩盖问题。
- 吞掉异常。
- 绕过 Mainnet/Testnet 安全保护。
- 执行真实资金操作。
- 未执行测试却声称通过。
- 未完成 S-201～S-203 却声称预设问题解决。

## 四、阶段 0：基线与真实写单者确认

修改任何代码前执行：

```bash
git branch --show-current
git rev-parse HEAD
git status --short
git log -5 --oneline
python --version
```

不得覆盖已有未提交改动。

读取：

- 实际 `.env`
- PowerShell 启动脚本
- Compose/K8s/Systemd override
- 当前进程环境
- 当前运行数据库

执行：

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

执行：

```bash
rg -n "AUTOMATED_TRADING_ENGINE" .env* scripts infra docker-compose*.yml
```

如果能访问运行数据库，使用现有脚本或只读查询抽样最近三天的开仓订单，读取：

```text
decision_variant
testnet_sampling_mode
candidate_id
requested_leverage
requested_notional
order_type
config_snapshot_id
config_hash
```

必须在报告中分别写：

```text
engine flag:
V2 activation:
allow legacy writer:
actual recent writer:
evidence:
```

分支规则：

### A. legacy

继续 S-101。

### B. v2_shadow 且 allow_legacy_writer=True

继续 S-101，并写：

```text
引擎标志为 v2_shadow；V2 只运行 Shadow；实际交易所 writer 为 legacy。
```

### C. v2_active 或最近订单明确来自 V2

停止 S-101～S-203。

输出：

```text
DEVIATION-ENGINE-001
冻结方案假设：legacy 为实际 writer
当前实际情况：V2 Active 为实际 writer
是否修改代码：否
```

继续生成报告，不自行设计 V2 修复。

### D. 无法核实部署环境

按仓库标准桌面路径：

```text
v2_shadow + legacy writer
```

继续实施，但报告必须写：

```text
未能访问实际部署环境；按仓库标准桌面启动路径实施。
```

## 五、方案实施通用流程

每个 S-ID 必须严格按以下步骤执行：

1. 记录允许修改文件。
2. 记录禁止修改文件。
3. 运行修改前复现。
4. 建立失败测试。
5. 确认测试因目标问题失败。
6. 实施最小修改。
7. 运行该方案定向测试。
8. 运行直接相关回归。
9. `git diff --check`。
10. 创建独立 Commit。

提交格式：

```text
fix(S-101): ...
fix(S-102): ...
test(S-203): ...
```

一个 Commit 不得混入多个无关方案。

如果某一步无法按冻结方案实现，输出 DEVIATION，不得自行扩大范围。

## 六、严格实施顺序

依次执行：

```text
S-101
S-102
S-103
S-104
S-105
S-201
S-202
S-203
```

不得跳过 S-201～S-203。

### S-101

按冻结方案：

- sampling fallback 新默认改为 False。
- 显式 False 跨 bootstrap 保留。
- 增加 T-101、T-102。
- 不删除 sampling decision 代码。

### S-102

按冻结方案：

- 先计算 close_only。
- 新开仓 set_leverage 失败抛 `LEVERAGE_CONFIGURATION_FAILED`。
- `create_order()` 不得执行。
- ReduceOnly 不受阻。
- 不新增数据库状态机。

### S-103

按冻结方案恢复：

```python
pretrade_min_price_drift_bps = 20.0
pretrade_atr_drift_fraction = 0.25
```

增加 T-301。

### S-104

删除：

```python
reference_price * 0.0015
```

sampling notional 仅为真实 `min_notional`。

增加 T-401。

### S-105

sampling 只保留 decision/action，不拥有正式仓位权限。

必须保证：

- flat + sampling：不调用 Gatekeeper/Gateway，不创建仓位。
- formal position + sampling opposite：不平仓。
- formal primary opposite：原行为不回归。
- rank_dropout 不回归。

增加 T-501、T-502、T-503。

### S-201

按冻结 operator key 清单修复 bootstrap。

必须保留显式：

```text
False
0
空列表
```

不得使用 truthy 判断。

增加 T-601～T-603。

### S-202

只修改现有：

```text
_requested_leverage
_requested_notional
```

不得新增第二套 sizing service。

冻结优先级必须与 `02-frozen-solution-v1.0.md` 完全一致。

增加 T-701～T-704。

### S-203

建立真实合同测试：

```text
API 保存
→ NEXT_CYCLE snapshot
→ 激活
→ bootstrap
→ 再激活
→ primary 非 sampling 生成订单
```

断言 operator 设置、ConfigSnapshot 和订单请求一致。

## 七、偏差规则

出现以下情况时，暂停对应方案：

- 文件或函数不存在。
- 函数签名已经改变。
- 当前 HEAD 已经完整修复问题。
- 只有修改 V2 才能继续。
- 需要数据库迁移。
- 需要新增依赖。
- 需要修改策略逻辑。
- 无法建立有效失败测试。
- 当前问题不能复现。
- 冻结方案会破坏平仓/ReduceOnly。
- 必须扩大文件范围才能继续。

偏差格式：

```text
DEVIATION-001
对应问题：
对应方案：
冻结要求：
当前代码：
冲突证据：
为什么无法继续：
最小调整建议：
是否修改代码：否
当前安全状态：
```

其他独立方案可以继续。

## 八、必须执行的测试

阶段 1：

```bash
pytest -q \
  tests/services/test_directional_sampling_fallback.py \
  tests/services/test_binance_gateway.py \
  tests/services/test_paper_runtime.py \
  tests/services/test_execution_truth.py \
  tests/services/test_paper_bootstrap.py
```

阶段 2：

```bash
pytest -q \
  tests/services/test_paper_bootstrap.py \
  tests/services/test_paper_signal.py \
  tests/services/test_asset_risk_tiers.py \
  tests/api/test_paper_runtime_api.py
```

合并定向回归：

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

静态检查：

```bash
ruff check .
ruff format --check .
mypy
git diff --check
```

全量：

```bash
pytest -q -m "not integration"
```

未执行必须写：

```text
未执行，不得视为通过。
```

## 九、范围自检

提交前执行：

```bash
git diff --name-only <start_commit>..HEAD
git diff --stat <start_commit>..HEAD
git diff --check <start_commit>..HEAD
rg -n "DEGRADED: per-symbol" services/automated_trading/application/entry_service.py
```

确认未修改：

```text
services/automated_trading/application/entry_service.py
services/automated_trading/application/cycle_service.py
services/automated_trading/application/decision_service.py
V2 binance adapter
MACD/EMA/ADX
数据库迁移
前端
依赖文件
```

确认没有：

- 新 sizing service。
- 新状态真源。
- 旧逻辑与新逻辑竞争。
- 无关格式化。
- 测试标准下降。

## 十、提交与推送

每个方案测试通过后创建独立 Commit。

如果用户已授权推送：

```bash
git push
```

不得推送测试失败的提交。

如果推送失败，保留本地安全 Commit，报告实际原因。

## 十一、最终输出

生成：

```text
03-implementation-verification-report.md
```

必须包含：

### 1. 执行结论

只能选择：

- 全部冻结方案已实施并通过验收。
- 部分方案完成，存在偏差或阻塞。
- 方案与代码不一致，未进行高风险修改。
- 测试未通过，任务不能判定完成。
- 没有需要执行的修改。

### 2. 基线

- 分支
- 起始 Commit
- 结束 Commit
- 原始工作区状态
- 执行环境
- engine flag
- V2 activation
- allow legacy writer
- actual writer

### 3. 实施结果

逐项：

```text
S-101
对应问题：
修改文件：
实际修改：
修改前复现：
失败测试：
通过测试：
验收：
Commit：
状态：
```

直到 S-203。

### 4. 代码变更清单

每个文件说明：

- 对应问题
- 对应方案
- 根因
- 测试
- 验收项

### 5. 测试证据

每条命令记录：

- 命令
- exit code
- passed
- failed
- skipped
- 关键输出

### 6. 验收矩阵

| 问题 | 方案 | 测试 | 验收 | 结果 | 证据 |
|---|---|---|---|---|---|

### 7. 偏差报告

没有偏差时：

```text
没有发现需要偏离冻结方案的情况。
```

### 8. 未完成事项

只列冻结方案未完成项，不得新增建议。

### 9. 范围合规

明确回答：

- 是否修改允许范围外文件
- 是否修改 DEGRADED
- 是否修改 V2
- 是否修改买卖点
- 是否新增功能
- 是否升级依赖
- 是否存在重复实现
- 是否存在旧错误路径残留

### 10. 最终交付判断

只有 S-101～S-203、定向测试、静态检查和全量测试全部通过，才可以写：

```text
没有剩余属于本冻结范围的 P0/P1。
杠杆/仓位预设失效与采样越权已按冻结方案关闭。
达到停止条件，不再继续修改。
```

否则只写唯一阻塞，不得扩大后续范围。

现在开始执行。不得重新设计，不得扩展范围。
