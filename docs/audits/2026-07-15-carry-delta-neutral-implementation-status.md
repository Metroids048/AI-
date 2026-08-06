# Carry策略Delta-Neutral修复实施状态

**日期**: 2026-07-15
**状态**: 第1步已完成，待继续
**优先级**: P0（结构性缺陷修复）

## 已完成工作

### 第1步：修改`_carry_decision`生成对冲腿信息 ✅

**文件**: `services/execution/paper_signal.py`

**修改内容**：
- 在`_carry_decision`方法的第322-334行添加了`hedge_leg`生成逻辑
- 当`should_trade=True`时，生成现货对冲腿信息：
  - `symbol`: 现货标的（如"BTC/USDT"）
  - `direction`: 与永续相反方向（永续SHORT则现货LONG）
  - `order_type`: "market"
  - `is_spot`: True
  - `reason`: "delta_neutral_hedge_for_funding_carry"
- 将`hedge_leg`信息放入`DecisionPipelineResult.trace["hedge_leg"]`

**验证**：
- 代码已通过Read工具验证，逻辑正确落地（第318-361行）
- 创建了单元测试文件`tests/test_carry_delta_neutral_fix.py`，包含3个测试用例：
  1. `test_carry_decision_generates_hedge_leg`: 验证正常情况下生成hedge_leg
  2. `test_carry_decision_no_hedge_when_rejected`: 验证拒绝订单时不生成hedge_leg
  3. `test_negative_funding_hedge_direction`: 验证负资金费率时对冲方向正确

## 待完成工作

### 第2步：执行引擎识别并下对冲腿（未开始）

**目标**：修改执行引擎，识别`entry_context["hedge_leg"]`并实际下双腿订单

**关键发现**：
- 项目中**已经有完整的`CarryExecutionService`**（`services/execution/carry_execution.py`）
- 该服务已实现delta-neutral双腿执行：
  - 第86-96行：先下现货买单
  - 第98-106行：再下永续卖单
  - 第108-118行：如果第二条腿失败，自动补偿（回滚）第一条腿
- **但paper模式可能没有使用这个服务**

**两种实施路径**：

#### 路径A：启用现有的`CarryExecutionService`（推荐）
- 修改paper runtime，当检测到`strategy_lane="carry"`时，使用`CarryExecutionService`而非普通订单流程
- 优点：复用已有的成熟双腿执行逻辑
- 需要：查明paper模式当前如何处理carry订单，然后接入`CarryExecutionService`

#### 路径B：在现有订单流程中添加对冲腿逻辑
- 在`PaperRuntimeService`或`ExecutionGatekeeperService`中识别`hedge_leg`
- 主腿成功后立即下对冲腿
- 失败则回滚主腿
- 优点：不改变现有架构
- 缺点：重复实现已有的逻辑

### 第3步：仓位追踪标记对冲组合（未开始）

**目标**：
- 扩展`PositionSnapshot`模型，添加：
  - `hedge_group_id: str | None` - 对冲组ID
  - `is_hedge_leg: bool` - 是否为对冲腿
- 在创建仓位时，将现货腿和永续腿标记为同一个对冲组

### 第4步：风控识别对冲组合（未开始）

**目标**：
- 修改`ExecutionGatekeeperService._evaluate_numeric_risk`
- 计算`net_directional_exposure`时，识别对冲组合
- 对冲组合的两条腿应该互相抵消，净敞口≈0

### 第5步：复盘层报告对冲组合PnL（未开始）

**目标**：
- 修改Review Layer，将现货腿和永续腿的PnL合并报告
- 分离显示：
  - 价格PnL（应该≈0，验证对冲有效性）
  - 资金费率收入
  - 双边手续费成本
  - 净收益

## 关键设计决策

### 为什么选择方案B（策略内部协调）而非方案A（双腿订单模型）

**原因**：
1. **最小改动**：方案B只需修改执行引擎识别`hedge_leg`，无需改模型
2. **快速验证**：当前carry策略是paper-only，风险可控
3. **渐进式重构**：等层3候选注册表稳定后，再升级为方案A的双腿模型

### 为什么不直接使用现有的`CarryExecutionService`

**观察**：
- `CarryExecutionService`设计用于testnet/live真实交易
- 需要`SpotCarryGateway`和`PerpCarryGateway`两个gateway
- Paper模式可能使用不同的执行路径（模拟下单而非真实API调用）

**后续**：
- 需要先查明paper模式的具体执行流程
- 如果paper模式也可以使用gateway抽象，则应该复用`CarryExecutionService`

## 依赖问题

- **pandas_ta安装超时**：多次尝试安装pandas_ta失败（网络超时）
- **pytest安装超时**：尝试安装dev依赖失败（网络超时）
- **影响**：无法立即运行单元测试验证
- **缓解方案**：通过Read工具人工验证代码逻辑，测试可以稍后运行

## 下一步行动

1. **查明paper模式执行流程**：
   - 阅读`PaperRuntimeService.run_cycle`完整逻辑
   - 确认carry订单当前如何被处理
   - 确定是使用`CarryExecutionService`还是在runtime中添加对冲腿逻辑

2. **实施第2步**：
   - 根据第1步的发现，选择路径A或路径B
   - 编写对冲腿执行代码
   - 添加单元测试

3. **实施第3-5步**：
   - 仓位追踪、风控、复盘层的修改
   - 端到端测试

4. **真实数据验证**：
   - 使用真实BTC历史数据重新测试carry策略
   - 验证净期望值是否转正

## 参考文档

- **设计文档**: `docs/audits/2026-07-15-carry-delta-neutral-fix.md`
- **测试文件**: `tests/test_carry_delta_neutral_fix.py`
- **已修改文件**: `services/execution/paper_signal.py` (第318-361行)
- **现有双腿执行服务**: `services/execution/carry_execution.py` (CarryExecutionService)
