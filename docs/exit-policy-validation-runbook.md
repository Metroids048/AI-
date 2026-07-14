# Exit Policy Validation Runbook

**快速参考指南** | 最后更新: 2026-07-14

---

## 快速开始

### 对比两个 exit 策略（最常用）

```bash
# 默认：live baseline，最新 90 天，Fixed 2R vs ExitLadder
python -m scripts.compare_exit_policies_cli --days 90

# 自定义窗口
python -m scripts.compare_exit_policies_cli --days 60 --max-workers 16
```

**输出**: `docs/audits/YYYY-MM-DD-exitladder-replay-comparison.md`

---

## 常见任务

### 1. 回归验证（每周一次）

```bash
# 用冻结的 2026-07-12 配置复现审计数字（验证工具未退化）
python -m scripts.compare_exit_policies_cli \
  --entry-baseline frozen-2026-07-12 \
  --end-at 2026-07-12T08:00:00 \
  --reuse-stored-data \
  --days 90 \
  --output docs/audits/weekly-regression-$(date +%Y%m%d).md

# 检查关键指标（应与历史方向一致）
# Fixed 2R 净预期: 正数 (目标 ~0.0015)
# ExitLadder 净预期: 负数 (目标 ~-0.0012)
```

**预期结果**: 数字与 `docs/audits/2026-07-12-exitladder-replay-comparison.md` **方向一致**（Fixed 正 / Ladder 负），但绝对值可能因管线演进而偏移 5-10%。

### 2. 测试新 exit 策略参数

```python
# 示例：测试更激进的 Fixed 3R
from services.validation.technical_replay import compare_exit_policies, ExitPolicy, EXIT_MODE_FIXED_2R
from scripts.compare_exit_policies_cli import _base_rules, build_fixed_and_ladder_policies, run_comparison
from scripts.run_top20_technical_validation import _load_or_backfill, _closed_four_hour_boundary
from datetime import UTC, datetime

# 加载数据
market_data = _load_or_backfill(days=90, end_at=_closed_four_hour_boundary(datetime.now(UTC)))

# 定义策略
from shared.models import StrategyContract, StrategyRules, Timeframe
from services.execution.bootstrap import AUTO_PAPER_TECHNICAL_RULES

entry_config = StrategyContract(
    strategy_id="test-3r",
    strategy_key="test-3r",
    source="test",
    core_thesis="3R exit test",
    rules=StrategyRules(
        entry_rules=dict(AUTO_PAPER_TECHNICAL_RULES["entry_rules"]),
        stoploss_rules=dict(AUTO_PAPER_TECHNICAL_RULES["stoploss_rules"]),
        takeprofit_rules={},
        position_rules=dict(AUTO_PAPER_TECHNICAL_RULES["position_rules"]),
    ),
)

policy_2r = ExitPolicy(name="Fixed 2R", exit_mode=EXIT_MODE_FIXED_2R, exit_rules={}, takeprofit_rules={"risk_reward": 2.0})
policy_3r = ExitPolicy(name="Fixed 3R", exit_mode=EXIT_MODE_FIXED_2R, exit_rules={}, takeprofit_rules={"risk_reward": 3.0})

report = compare_exit_policies(
    entry_config=entry_config,
    exit_policy_a=policy_2r,
    exit_policy_b=policy_3r,
    market_data=market_data,
    warmup_bars=80,
    max_workers=8,
)

print(f"2R net_expectancy: {report.policy_a.net_expectancy:.6f}")
print(f"3R net_expectancy: {report.policy_b.net_expectancy:.6f}")
```

### 3. 调试回放崩溃

```bash
# 单 worker 模式，完整堆栈
python -m scripts.compare_exit_policies_cli --days 30 --max-workers 1 2>&1 | tee debug.log

# 检查数据完整性
python -c "
from scripts.run_top20_technical_validation import _load_stored
data = _load_stored(days=90)
for symbol, frames in data.items():
    for tf, bars in frames.items():
        print(f'{symbol} {tf}: {len(bars)} bars')
"
```

**常见错误**:
- `AttributeError: 'HistoricalMarketDataView' object has no attribute 'get_latest_market_extras'` → 已在模块 7 修复
- `KeyError: '15m'` → 数据回填不完整，删除 `.local/ohlcv_cache/` 重新回填
- `ProcessPoolExecutor` 超时 → 降低 `--max-workers` 或增加 `--days`

---

## 指标解读

### 关键指标

| 指标 | 说明 | 优秀阈值 |
|---|---|---|
| **Net expectancy** | 每单位风险的净期望收益 | >0.002 (优秀), >0 (可接受) |
| **Profit factor** | 总盈利 / 总亏损 | >1.2 (优秀), >1.0 (可接受) |
| **Win rate** | 胜率 | 30-50% (高 R 策略可接受低胜率) |
| **Max drawdown** | 最大回撤 | <0.6 (优秀), <1.0 (可接受) |
| **Avg hold hours** | 平均持仓时长 | 符合预期即可（ExitLadder 通常 2x Fixed 2R） |

### ExitLadder 特有指标

```
ladder_hits={'exit_ladder_1r': 223, 'exit_ladder_1.5r': 154}
```
- `exit_ladder_1r`: 1.0R 触发次数（部分平仓 40%）
- `exit_ladder_1.5r`: 1.5R 触发次数（部分平仓 30%）
- 剩余 30% 由 2.5R 追踪止损管理

**健康检查**: 如果 `1r` 触发次数远大于 `1.5r`（如 300 vs 50），说明多数单子在 1.0R-1.5R 之间反转 → ladder 提前止盈保护了部分利润，但也可能截断了趋势。

---

## 故障排查

### 回放数字突变

**症状**: 同样参数，上周 `net_expectancy=0.0015`，本周突然 `-0.005`。

**排查步骤**:
1. 检查 `AUTO_PAPER_TECHNICAL_RULES` 是否被修改（`git diff HEAD~7 services/execution/bootstrap.py`）
2. 检查 DecisionPipeline 是否有逻辑变更（`git log --since='7 days ago' services/execution/decision_pipeline.py`）
3. 对比两次审计报告的 `methodology` 字段
4. 用 `--entry-baseline frozen-2026-07-12` 跑回归 → 如果冻结配置下数字稳定，说明是 live 配置变更导致

### 回放与实盘偏差

**症状**: 回放显示 `net_expectancy=+0.002`，但实盘 7 天表现为负。

**可能原因**:
1. **滑点/费率假设偏乐观**: 回放用固定 `core_fee_bps=5 / standard_fee_bps=5`，实盘可能遇到流动性不足
2. **信号延迟**: 回放假设信号立即执行，实盘有 order placement → fill 的延迟
3. **市场regime切换**: 回放窗口是上升/震荡期，实盘遇到单边下跌
4. **meta-label 模型过拟合**: 回放用历史训练的模型，实盘分布已漂移

**行动**:
- 对比回放与实盘的 `signal_count` / `win_rate` → 定位是信号质量还是执行问题
- 增加回放的 `core_slippage_bps` / `standard_slippage_bps` 模拟最坏情况
- 滚动窗口验证（多个 30 天子窗口）→ 检查策略在不同 regime 下的稳健性

---

## 最佳实践

### 1. 版本控制审计报告

```bash
# 每次重要回放后，提交审计文档
git add docs/audits/*.md
git commit -m "chore(audit): weekly exit-policy validation $(date +%Y-%m-%d)"
git tag "audit-$(date +%Y%m%d)"
git push origin main --tags
```

### 2. 定期回归测试

```bash
# 每周日自动运行（crontab）
0 0 * * 0 cd /path/to/project && python -m scripts.compare_exit_policies_cli --entry-baseline frozen-2026-07-12 --end-at 2026-07-12T08:00:00 --reuse-stored-data --days 90 --output /tmp/regression.md && diff -u docs/audits/2026-07-12-exitladder-replay-comparison.md /tmp/regression.md || echo "Regression drift detected!"
```

### 3. 决策检查清单

在基于回放结果调整实盘配置前，确认：
- [ ] 回放窗口 ≥90 天（避免偶然性）
- [ ] 净预期差异 >0.001（统计显著）
- [ ] 最大回撤可接受（<1.0R）
- [ ] 平均持仓时长符合策略预期
- [ ] 信号数量足够（>100，避免小样本）
- [ ] 方向性结论在多个子窗口保持一致
- [ ] 已在 Testnet 验证 7 天无异常

---

## 联系与支持

- **文档**: `docs/technical-validation-framework.md`（完整架构文档）
- **代码**: `services/validation/technical_replay.py`, `scripts/compare_exit_policies_cli.py`
- **测试**: `pytest tests/services/test_technical_strategy_validation.py -v`
- **问题追踪**: Git issues 或内部 Slack #quant-validation

---

**快速记忆卡片**:
```
日常验证    → python -m scripts.compare_exit_policies_cli --days 90
回归验证    → ... --entry-baseline frozen-2026-07-12 --end-at 2026-07-12T08:00:00
调试单 worker → ... --max-workers 1
查看帮助    → python -m scripts.compare_exit_policies_cli --help
```
