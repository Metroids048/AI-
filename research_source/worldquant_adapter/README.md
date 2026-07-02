# WorldQuant Adapter — 方法论移植（非表达式搬运）

## 定位

本地 `Desktop/alpha` 是一套成熟的 WorldQuant Brain **美股**因子挖掘流水线
（约 67 万条表达式、约 8500 条带 Sharpe/Fitness/Turnover 的候选）。这些表达式
基于美股基本面字段（`capex_to_total_assets`、`debt_lt` 等），**不能直接套用到
平台第一阶段主市场 BTC/USDT 永续**。

因此本模块的职责是 **移植方法论，而不是搬运表达式**：

- 提取算子词表（`rank` / `ts_delta` / `group_rank` / `correlation` ...）→ `operators.py`
- 用纯 pandas/numpy 在加密 OHLCV 序列上重新实现这些算子（不依赖 TA-Lib、不依赖基本面）
- 把表达式解析为结构化 `AlphaPlan`（`shared.models.AlphaPlan`，默认 `target_market=crypto_perp`）→ `expression_parser.py`
- 由 `CryptoFactorGenerator` 生成可在 BTC/USDT 上回测的因子信号代码 → `crypto_factor_generator.py`

## 约束

- AGENTS.md 不可谈判项 #5：WorldQuant 是策略**来源**，不是平台主干。
- 框架隔离：本模块只被 `services/strategy_library/importers/` 消费，
  **不被 `apps/api/` import**。
- 不上传 Brain session 密钥；只通过 `.env` 的 `WORLDQUANT_ALPHA_LOCAL_PATH`
  引用本地路径。

## 状态

Phase 0 仅落地接口接缝（stub）。实现见 Phase 1 任务 P1-03。
