# WorldQuant Adapter

## 定位

本目录只负责把本地 `Desktop/alpha` 的 WorldQuant 方法论迁移到加密研究语境，不直接搬运美股表达式进入执行链路。

- 算子库：`operators.py`
- 表达式解析：`expression_parser.py`
- 表达式执行：`expression_evaluator.py`
- 因子代码生成：`crypto_factor_generator.py`
- 本地 alpha intake：`local_alpha_scanner.py`

## 当前支持边界

- 支持的基础输入：`open/high/low/close/volume/vwap/returns/adv20/funding_rate/open_interest/long_ratio/short_ratio/liquidation_usd`
- 支持的运算：字面量、标识符、一元负号、`+ - * /`、以及已登记算子的嵌套调用
- 支持的新增算子：`ts_rank`、`ts_zscore`、`group_neutralize`
- 显式分组映射：
  - `industry -> volatility_regime`
  - `sector -> funding_regime`
  - `subindustry -> liquidity_regime`
  - `market -> market`

不支持的股票基本面字段或未登记运算符会被明确标记为 unsupported，并在 evaluator 执行时直接报错，不再使用占位 fallback。

## Intake 与审计

- 本地 alpha intake 会保留 `raw_expression`、算子列表、窗口参数、分组别名、`behavior_signature`、支持/不支持输入与运算符。
- 可执行表达式会进入 `rule_candidate`。
- 不可执行或 evaluator 明确拒绝的表达式会进入 `subjective_to_drop`，并把拒绝证据写入 intake 元数据，供后续 Review/聚类复用。

## 约束

- WorldQuant 仍然只是研究来源，不是平台主干。
- 本模块产出的是研究/策略素材，不允许绕过 Validation Layer 直接进入 Execution Layer。
- 运行时只依赖本地 alpha 路径与 crypto-native 数据上下文，不上传 Brain 会话或私密凭证。
