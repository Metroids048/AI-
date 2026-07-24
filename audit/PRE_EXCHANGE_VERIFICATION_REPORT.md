# 交易所边界前完整验证报告

生成时间：2026-07-24T07:30:18.998303+00:00

## 结论

**可以确认：最新代码中的 BTC/ETH 自动方向链路能够从正常 RuntimeScheduler 周期，经真实指标计算、主策略拒绝后的受限 Testnet 采样候选、Ensemble/Meta Label、Gatekeeper、TradeIntent、市场规则快照、订单标准化，完整调用交易所适配器 `submit_order/create_order` 边界，并在收到确认成交价和成交量后建立本地映射。**

**不能确认：真实 Binance Demo 网络、凭据、账户模式及真实 order ID。上传代码不含 `.env`，本环境没有操作方凭据，验证器明确拒绝冒充真实订单。**

因此准确状态是：`PRE_EXCHANGE_PATH_VERIFIED`，不是 `REAL_BINANCE_VERIFIED`。

## 新鲜验证结果

| 验证 | 结果 | 证据 |
|---|---:|---|
| 定向交换所优先链路 | 13 passed | `01_verify_directional_exchange_first.log`、JSON artifact |
| 扩大后的前置链路回归 | 235 passed | `04_pre_exchange_regression_with_celery_contract_stub.log` |
| Python 全目录编译 | exit 0 | `05_compileall.log` |
| 正常调度器服务级证明 | PASS；BTC/ETH 各1笔自然候选；2个持仓保持开放 | `02_verify_natural_directional_service.log`、`natural-service-proof.json` |
| Acceptance/手工订单污染 | 0 | `natural-service-summary.json` |
| FastAPI读取订单/仓位 | health ok；2 orders；2 positions | `natural-service-proof.json` |
| 真实 Binance 凭据缺失时 | 明确拒绝，exit 1 | `06_real_binance_proof_refusal.log` |

## 服务级证明所经过的真实项目组件

1. `RuntimeScheduler` 正常周期调用，不使用 Testnet acceptance 或手工下单入口。
2. BTC/ETH 真实指标计算；主 `trend_momentum_v1` 因严格多周期分歧拒绝。
3. 仅在精确 BTC/ETH、`binance_simulation_first`、Testnet、自动执行已开启且非 mainnet 时，调用已有 `operator_heuristic_v2_relaxed` 采样候选。
4. 15m 入场方向必须至少得到 1h/4h 中一个同向确认。
5. 候选进入 Gatekeeper，附加 TradeIntent 与 MarketRulesSnapshot。
6. 订单进入标准化与交易所适配器边界。
7. 严格交易所模拟器返回成交状态、平均成交价、成交数量及保护单引用。
8. 本地订单和仓位只按确认成交数据建立。
9. FastAPI 服务启动并读取对应订单、仓位与运行状态。
10. 两笔仓位保持开放，未制造快速开平往返。

## 关键代码证据

- Testnet受限采样候选及安全范围：`services/execution/paper_signal.py:277-365`
- 15m + 至少一个高周期确认：`services/execution/decision_pipeline.py:430-485`
- 订单上下文与市场规则：`services/execution/order_context.py:17-85`
- Gatekeeper入口：`services/execution/gatekeeper.py:99+`
- 交易所适配器强制MarketRulesSnapshot及成交解析：`services/execution/gateway.py:418-520`
- 正常调度订单来源：`services/execution/paper_cycle_orchestrator.py:1174-1198`
- 交易所确认价用于本地投影：`services/execution/paper_cycle_orchestrator.py:2206+`
- 启动时同步最新策略快照：`services/execution/bootstrap.py:480-655`
- 真实订单证明器只接受方向调度订单并反查交易所：`scripts/prove_real_binance_natural_order.py:36-177`

## Celery说明

当前容器的内部Python源没有提供Celery安装包。第一次扩大回归为 `231 passed, 4 failed`，四项均为 `ModuleNotFoundError: celery`。随后使用独立临时目录中的最小 `shared_task` 合约桩重新执行同一套测试，结果为 `235 passed`。该桩没有修改项目代码，只验证 RuntimeScheduler 自身逻辑。

操作方设备提供的运行反馈已证明实际Scheduler正在运行且周期持续完成，因此Celery安装/启动不是当时零订单的已知阻断。不过，本报告不把最小桩视为完整Celery发行包集成测试。

## 明确未证明的内容

- 真实 Binance Demo `create_order` 网络请求成功；
- 真实 Binance order ID、成交状态和仓位；
- 操作方账户的 ONE_WAY/HEDGE、保证金模式与现有外部仓位交互；
- 基于真实行情自然出现候选所需时间；
- 策略收益率、盈亏比和长期回撤；
- 所有可能市场状态和故障分支绝对无Bug。

根据CCXT统一订单约定，成功的 `createOrder` 至少应返回订单 `id`，之后应使用该ID查询订单状态、`filled`与`average`。没有这些真实交易所字段，就不能声称真实交易所已跑通。

## 最终判定

- 配置/策略/调度/决策/Gatekeeper/上下文/标准化/适配器调用边界：**通过本地确定性验证**。
- 确认成交后本地映射与API读取：**通过严格交易所模拟器验证**。
- 真实 Binance Demo：**未验证，必须在带凭据的运行设备上由正常调度订单产生真实 order ID，并由 `scripts.prove_real_binance_natural_order` 反查确认。**
