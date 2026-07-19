# Paper Runtime 拆分独立复核报告

**日期**: 2026-07-19
**审查范围**: paper_runtime.py 从 2601 行拆分为 4 个文件的独立正确性复核
**审查者**: AI 独立代理（不参与原始拆分）

---

## 一、文件行数与职责摘要

| 文件 | 行数 | 主要类/函数 | 职责摘要 |
|------|------|------------|---------|
| `services/execution/paper_runtime.py` | 2530 行 | `PaperRuntimeService`、`ProtectiveLevels`、`ProtectiveTrigger`、`_fixed_universe_skip_reason`、`_estimated_transaction_cost`、`_is_reduce_only_already_flat`、`_parse_datetime` | 核心编排层：持有整条 paper 周期逻辑（保护触发、对账、Binance 执行、信号生成调用、仓位管理）。**拆分后仍是主文件**，是其余三个模块的组装点。 |
| `services/execution/paper_cycle_orchestrator.py` | 27 行 | `PaperCycleOrchestrator` | 薄封装层：接收 `cycle_runner` 可调用对象，将 `run_cycle()` 调用代理给它。当前无额外逻辑，预留扩展点（重试、熔断）。 |
| `services/execution/paper_exchange_execution.py` | 129 行 | `PaperExchangeExecutionService` | 交易所请求适配层：构建 gateway-safe 订单（close_only/reduce_only 语义转换）、失效限价单撤销。**不持有网关执行决策**，只做请求格式化。 |
| `services/execution/paper_order_lifecycle.py` | 228 行 | `PaperOrderLifecycleService`、`RealizedOutcome`、`EstimatedTransactionCost`、`realized_pnl()`、`estimated_transaction_cost()` | 本地纸面状态层：无网关依赖，负责本地 fill、开仓快照、平仓 PnL 计算、MtM 更新、手续费/滑点估算。 |

**拆分净效果**：原 2601 行被拆出约 384 行（129 + 228 + 27）。`paper_runtime.py` 从 2601 行降至 2530 行，下降约 2.7%。**核心体量几乎没有减少**，大量交易所交互逻辑（`_ensure_binance_execution`、`_reconcile_local_positions_with_exchange`、`_cancel_latest_entry_protections`、`_ensure_exchange_protections`）仍留在主文件。

---

## 二、Import 依赖关系

```
paper_runtime.py
  ├── imports paper_cycle_orchestrator  →  PaperCycleOrchestrator
  ├── imports paper_exchange_execution  →  PaperExchangeExecutionService
  └── imports paper_order_lifecycle     →  PaperOrderLifecycleService, EstimatedTransactionCost,
                                            RealizedOutcome, estimated_transaction_cost, realized_pnl

paper_cycle_orchestrator.py
  └── imports shared.models only        →  无跨模块内部依赖

paper_exchange_execution.py
  ├── imports services.strategy_library →  ExecutionRepository
  └── imports shared.models             →  ExecutionOrderRequest, PaperRun, PaperRuntimeAction, PositionSnapshot

paper_order_lifecycle.py
  ├── imports services.strategy_library →  ExecutionRepository
  └── imports shared.models             →  OrderExecution, PositionSnapshot, StrategyContract, TradeSide
```

**循环依赖检查结果**: 无循环依赖。依赖方向单向：`paper_runtime` → 三个子模块 → `shared.models/strategy_library`。

---

## 三、关键职责落地检查清单

| 职责 | 是否存在 | 所在文件 | 位置 |
|------|---------|---------|------|
| 对账逻辑（reconcile） | ✅ | `paper_runtime.py` | `_reconcile_local_positions_with_exchange()`（第 1582–1754 行），包含二次确认、孤立保护取消、交易所仅持仓恢复、本地幽灵清除 |
| Kill switch / 硬止损锁定 | ✅ | `paper_runtime.py` | `_is_hard_drawdown_locked()`（第 1238–1246 行）+ `_run_cycle()` 中 `hard_drawdown_locked` 分支（第 170–247 行），触发时强平所有持仓并将状态设为 `locked` |
| ReduceOnly 平仓逻辑 | ✅ | `paper_runtime.py` | `_ensure_binance_execution()`（第 2064–2239 行）+ `_is_reduce_only_already_flat()`（第 2518–2520 行），-2022 错误时确认交易所已平仓后本地清除幽灵仓位 |
| 失效限价单撤销 | ✅ | `paper_exchange_execution.py` | `expire_pending_limit_entries()`（第 71–128 行），由 `paper_runtime._expire_pending_limit_entries()` 在每轮开始时调用 |

所有四项关键职责均已落地，无遗漏。

---

## 四、发现的问题

### HIGH：拆分幅度严重不足——主文件仍为 2530 行

**描述**: `paper_runtime.py` 拆分后从 2601 行只降到 2530 行。多项重量级交易所交互责任仍留在主文件内：

- `_ensure_binance_execution()`（约 176 行）
- `_reconcile_local_positions_with_exchange()`（约 173 行）
- `_cancel_latest_entry_protections()`（约 54 行）
- `_cancel_orphan_exchange_protections()`（约 49 行）
- `_ensure_exchange_protections()`（约 105 行）

这五段逻辑语义上属于"交易所交互"，与 `PaperExchangeExecutionService` 的定位一致，但实际未被迁移过去。`PaperExchangeExecutionService` 目前只持有三个方法（`gateway_order_request`、`gateway_mirror_request`、`expire_pending_limit_entries`），职责分配严重不均衡。

**影响**: 文件超过 800 行的编码规范红线 3 倍以上；主要复杂逻辑无隔离，可维护性未改善。

---

### MEDIUM：PaperCycleOrchestrator 是空壳，不产生实际价值

**描述**: `paper_cycle_orchestrator.py` 全部 27 行仅做一件事：

```python
def run_cycle(self, *, paper_run_id, request):
    return self._cycle_runner(paper_run_id, request)
```

它不持有任何业务逻辑。`PaperRuntimeService.__init__` 中用 `self._run_cycle` 作为 `cycle_runner` 传入，意味着这层间接引用不带来任何额外能力。

**影响**: 引入了一个文件和一个类，增加读者认知成本，但当前零收益。如果设计意图是预留重试/熔断扩展点，应在代码注释中明确说明，并在文件头补充 TODOs。

---

### MEDIUM：测试对新模块边界覆盖为形式验证，缺乏行为测试

**描述**: `test_paper_runtime.py` 中对三个新文件的覆盖如下：

- `test_runtime_exposes_cycle_orchestrator`（第 1246–1254 行）：只检查 `callable(runtime.cycle_orchestrator.run_cycle)`，无行为测试。
- `test_runtime_exchange_execution_exposes_pending_limit_expiry`（第 1240–1243 行）：只检查 `callable(PaperExchangeExecutionService.expire_pending_limit_entries)`，无行为测试。
- `test_runtime_exposes_order_lifecycle_that_persists_fills`（第 48–63 行）：通过 `runtime.order_lifecycle.fill_order()` 做了一次实际调用，是唯一有行为覆盖的新模块测试。

`PaperOrderLifecycleService.open_position()`、`PaperOrderLifecycleService.close_position()`、`PaperOrderLifecycleService.mark_position()` 均无独立单元测试；`PaperExchangeExecutionService.gateway_order_request()` 的隔离测试通过 `test_gateway_close_request_preserves_position_direction_for_gateway_side_mapping` 做到但走的是 `PaperRuntimeService._gateway_order_request` 静态转发而非直接调用。

---

### LOW：静态转发方法制造噪声

**描述**: `paper_runtime.py` 中存在两个静态方法，其功能只是把调用转发给 `PaperExchangeExecutionService`：

```python
# paper_runtime.py 第 2282–2300 行
@staticmethod
def _gateway_order_request(...):
    return PaperExchangeExecutionService.gateway_order_request(...)

@staticmethod
def _gateway_mirror_request(...):
    return PaperExchangeExecutionService.gateway_mirror_request(...)
```

这两个方法存在是为了向后兼容内部调用点，但它们让读者误以为逻辑在此而非在 `paper_exchange_execution.py`。

---

### LOW：`_estimated_transaction_cost` 私有包装存在混淆风险

**描述**: `paper_runtime.py` 第 2497–2504 行有一个私有包装函数：

```python
def _estimated_transaction_cost(...):
    return estimated_transaction_cost(...)
```

`test_paper_runtime.py` 第 10 行从 `paper_runtime` 导入此函数用于测试：

```python
from services.execution.paper_runtime import (
    _estimated_transaction_cost,
    ...
)
```

实际逻辑在 `paper_order_lifecycle.py` 的 `estimated_transaction_cost()`。测试应直接从 `paper_order_lifecycle` 导入，否则未来删除包装层会破坏测试。

---

## 五、结论

**拆分结论: 部分合格，需要修正**

### 合格项
- 无循环依赖，依赖方向正确
- `run_cycle()` 和 `get_runtime_status()` 公开接口完整保留
- 四项关键职责（对账、kill switch、ReduceOnly、失效限价单）均在正确位置落地
- `paper_order_lifecycle.py` 职责清晰，无网关依赖，可独立测试
- `paper_exchange_execution.py` 的 `expire_pending_limit_entries` 隔离合理

### 需要修正项

**优先级 HIGH**:
1. 考虑将 `_ensure_binance_execution`、`_reconcile_local_positions_with_exchange`、`_cancel_latest_entry_protections`、`_cancel_orphan_exchange_protections`、`_ensure_exchange_protections` 迁移到 `PaperExchangeExecutionService`，使主文件降至合理规模（目标 <800 行）。

**优先级 MEDIUM**:
2. 为 `PaperCycleOrchestrator` 补充明确的扩展意图注释（TODO 列出预期的重试/熔断逻辑），否则考虑直接删除这个空壳类。
3. 为 `PaperOrderLifecycleService` 的 `open_position()`、`close_position()`、`mark_position()` 补充独立单元测试，确保本地状态操作与主循环解耦验证。

**优先级 LOW**:
4. 将 `_estimated_transaction_cost` 测试导入点从 `paper_runtime` 改为直接从 `paper_order_lifecycle` 导入 `estimated_transaction_cost`。
5. 清理 `_gateway_order_request` / `_gateway_mirror_request` 静态转发方法，在 `_ensure_binance_execution` 内直接调用 `self.exchange_execution.gateway_order_request()`。

### 建议下一步
在当前测试绿灯的前提下，将交易所交互方法批量迁移至 `PaperExchangeExecutionService` 是安全可做的后续任务。由于测试套件已覆盖 reconcile/ReduceOnly/kill switch 的集成行为，迁移风险可控。迁移完成后，`paper_runtime.py` 应可降至 500–700 行范围，达成真正意义上的拆分。
