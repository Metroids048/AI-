# HOTFIX-001: Carry 对冲腿方法缩进错误修复

## 问题描述

**发现时间**: 2026-07-15
**严重程度**: CRITICAL
**影响范围**: Carry 策略 100% 无法开单

### 根本原因

`_create_hedge_order_request` 和 `_mark_position_as_hedged` 这两个方法因为**缩进错误**被嵌套在模块级函数 `_parse_datetime` 内部，而不是作为 `PaperRuntimeService` 类的方法存在。

**文件位置**: `services/execution/paper_runtime.py`
**错误行号**: 2133-2204 (修复前)

### 实际影响

1. **运行时错误**: 当 carry 策略尝试创建对冲腿时，`self._create_hedge_order_request(...)` 抛出 `AttributeError`
2. **静默失败**: 异常被外层 `except Exception` 捕获，订单以 `hedge_leg_exception: ...` 被拒绝
3. **结果**: Carry 策略 100% 开不出单，delta-neutral 对冲机制完全失效

### 测试问题

**测试文件**: `tests/test_carry_delta_neutral_fix.py`

测试本身也有错误，从未成功运行过：
- 使用了错误的 `StrategyContract` 字段（`version_id`、错误的 `market` 枚举）
- `PaperRunStepRequest` 使用了不存在的 `paper_run_id` 字段
- 这些错误导致测试在数据构造阶段就失败，无法检测到缩进 bug

## 修复内容

### 1. 修复方法缩进 (paper_runtime.py)

**修复前** (2133-2204行):
```python
def _parse_datetime(value: object) -> datetime | None:
    # ... function body ...
    return None

    def _create_hedge_order_request(  # ← 4个空格，嵌套在 _parse_datetime 内
        self,
        ...
    ) -> ExecutionOrderRequest:
```

**修复后**:
```python
def _parse_datetime(value: object) -> datetime | None:
    # ... function body ...
    return None

# PaperRuntimeService 类内部 (约1986行之后)
class PaperRuntimeService:
    # ... 其他方法 ...

    def _create_hedge_order_request(  # ← 现在是类的方法
        self,
        ...
    ) -> ExecutionOrderRequest:
```

**具体操作**:
1. 从 2133-2204 行删除这两个方法
2. 将它们添加到 `PaperRuntimeService._mark_position` 方法之后（约 2006 行）
3. 确保正确的缩进级别（4 个空格，与其他类方法一致）

### 2. 修复测试数据构造 (test_carry_delta_neutral_fix.py)

**修复前**:
```python
StrategyContract(
    strategy_id="test_carry",
    version_id="v1",           # ← 错误：不是直接字段
    market="crypto",           # ← 错误：应该是 Market 枚举
    ...
)

PaperRun(
    strategy_key="test_carry_v1",  # ← 错误：应该是 strategy_id
    ...
)

PaperRunStepRequest(
    paper_run_id="test_run",   # ← 错误：没有这个字段
    perp_symbol="BTC/USDT:USDT",
)
```

**修复后**:
```python
StrategyContract(
    strategy_key="test_carry_v1",
    strategy_id="test_carry",
    market=Market.CRYPTO_PERP,  # ← 正确的枚举
    ...
)

PaperRun(
    strategy_id="test_carry",
    version_id="v1",
    ...
)

PaperRunStepRequest(
    symbol="BTC/USDT",          # ← 正确的字段
    perp_symbol="BTC/USDT:USDT",
)
```

### 3. 修复负资金费率测试

负资金费率测试失败是因为 `funding_bps < threshold_bps` 检查（-1.0 < 0.5）会拒绝负资金费率。

**修复**: 将阈值设置为负数以允许负资金费率通过：
```python
carry_strategy.rules.entry_rules["funding_threshold_bps"] = -2.0
```

## 验证结果

### AST 验证
```
✓ _create_hedge_order_request 和 _mark_position_as_hedged 现在是 PaperRuntimeService 类的方法
```

### 测试结果
```
tests/test_carry_delta_neutral_fix.py::test_carry_decision_generates_hedge_leg PASSED
tests/test_carry_delta_neutral_fix.py::test_carry_decision_no_hedge_when_rejected PASSED
tests/test_carry_delta_neutral_fix.py::test_negative_funding_hedge_direction PASSED
```

## 为什么会发生这种情况？

这次修复暴露了严重的**交付质量问题**：

### 1. 代码审查缺失
- 缩进错误是一个**显而易见**的语法级错误
- 任何代码审查工具或人工审查都应该立即发现
- 说明修复代码没有经过基本的静态检查

### 2. 测试从未运行
- 测试代码本身就有错误，从未成功执行过
- 这意味着"已测试"的声明是**虚假的**
- 违反了基本的 TDD 原则：先写测试，确保测试能跑

### 3. 交付自查失败
- 同一个 commit 包含文档强调"必须做交付自查"
- 但这次修复本身就没有做交付自查
- 说明流程文档和实际执行存在严重脱节

### 4. 重复性问题
- 这不是第一次出现"声称修复但实际未生效"的问题
- 说明根本原因（缺乏验证机制）没有被系统性解决

## 根本性改进建议

### 1. Pre-commit Hook（强制）
```bash
# .git/hooks/pre-commit
python -m pytest tests/ --tb=short
python -m ruff check .
python -m mypy services/ shared/
```

### 2. CI/CD 门槛
- 所有 PR 必须通过完整测试套件
- 禁止跳过测试或标记为 "skip"
- 覆盖率门槛：新代码 80%+

### 3. 交付检查清单（强制执行）
在声称完成之前：
- [ ] 用 Read 工具重新读取所有修改的代码
- [ ] 运行相关测试并查看输出
- [ ] 用 AST/grep 验证预期结构
- [ ] 在真实环境中运行一个周期

### 4. 测试优先原则
- 禁止"先写代码再补测试"
- 测试必须先失败（RED），再通过（GREEN）
- 测试失败的原因必须是逻辑错误，而不是测试代码错误

## 下一步行动

1. ✅ **立即**: 修复缩进和测试 (已完成)
2. **紧急**: 解决数据库连接问题（仍然是 `timescaledb` 主机解析失败）
3. **短期**: 在真实环境运行几个周期，验证对冲腿实际开单
4. **中期**: 重新测量 carry 策略的净期望值（之前的负值是裸头寸版本）
5. **长期**: 建立强制性 pre-commit 验证和 CI 门槛

## 影响的文件

- `services/execution/paper_runtime.py` (修复缩进)
- `tests/test_carry_delta_neutral_fix.py` (修复测试)
- `docs/hotfix/HOTFIX-001-carry-hedge-indentation.md` (本文档)

## 相关 ADR/任务

- ADR-063: Delta-neutral carry 修复（本次修复的来源）
- TASK-059: 数据库连接问题（仍未解决）
- 模块 0: 核查流程（需要重新运行）

---

**修复人员**: AI Assistant
**审查状态**: 待人工确认
**优先级**: CRITICAL
**状态**: 已修复，待验证
