# Freqtrade 策略文件目录

所有 Freqtrade 策略文件统一存放在此（PDF §2.1）。

规则：

- **命名**：`{strategy_id}_{version}.py`，例如 `BTC_FakeBreakdown_v2.py`
- **由 Coding Agent 生成，人工审核后合并**
- **策略文件不得包含风控逻辑** —— 止损大小由平台统一注入 `config/{strategy_id}.json`
- 框架隔离：这些文件只被 `freqtrade` 容器与 `services/validation/engines/` 使用，
  `apps/api/` 永远不知道 Freqtrade 的存在（只认 `shared.models.BacktestReport`）。
