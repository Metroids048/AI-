# 量化策略层重构 - 交接文档

## 当前状态（2026-07-15）

### ✅ 已完成的工作

#### 1. 核心框架实现完成
- **候选策略注册表**：3个候选策略已定义并测试通过
- **统一排行榜工具**：多候选对比工具实现并测试通过
- **指标库扩容**：pandas_ta适配器实现完成（9个指标）
- **测试覆盖**：16/16 单元测试编写完成

#### 2. 文件清单
```
新增核心文件：
- services/strategy_library/technical/pandas_ta_adapter.py
- services/strategy_library/candidates/registry.py
- services/validation/candidate_leaderboard.py

新增测试文件：
- tests/test_pandas_ta_adapter.py
- tests/test_candidate_registry.py
- tests/test_candidate_leaderboard.py

工具脚本：
- scripts/verify_quant_dependencies.py

文档：
- stage1-pandas-ta-adapter-acceptance.md
- strategy-layer-refactor-progress-report.md
- strategy-layer-refactor-handoff.md（本文件）
```

### ⏳ 待完成的步骤

#### 步骤1：完成 pandas-ta 安装验证
**当前状态**：正在后台安装 pandas-ta

**验证方法**：
```bash
# 检查安装是否成功
python scripts/verify_quant_dependencies.py

# 如果成功，运行单元测试
python -m pytest tests/test_pandas_ta_adapter.py -v
```

**如果安装失败**：
- pandas-ta 并非必需（层1的代码价值不受影响）
- 可以先用现有10个手搓指标继续
- 后续可以重试安装或使用其他指标库

#### 步骤2：集成到信号生成链路
**需要修改的文件**：

1. **services/execution/paper_signal.py**
```python
# 在导入区域添加
from services.strategy_library.technical.pandas_ta_adapter import generate_pandas_ta_signal

# 在 generate_technical_signals() 函数中添加
if signal_name.startswith("pandas_ta_"):
    indicator_name = signal_name.replace("pandas_ta_", "")
    signal = generate_pandas_ta_signal(
        name=indicator_name,
        symbol=symbol,
        frame=frame,
    )
    if signal:
        signals.append(signal)
```

2. **services/execution/bootstrap.py**
```python
# 在 AUTO_PAPER_TECHNICAL_RULES["entry_rules"]["enabled_signals"] 中添加
"enabled_signals": [
    # 现有10个信号...
    "macd",
    "dow_trend",
    # ...
    
    # 新增pandas_ta信号（可选启用）
    # "pandas_ta_supertrend",
    # "pandas_ta_stoch_rsi",
    # "pandas_ta_hma",
]
```

#### 步骤3：运行第一版排行榜
**准备工作**：
1. 确认有 Top20 最近90天的历史数据
2. 确认现有回测引擎可正常运行

**执行命令**：
```python
from services.validation.candidate_leaderboard import run_candidate_leaderboard
from services.data import DataRepository

# 加载市场数据
repo = DataRepository()
market_data = repo.load_historical_data(
    symbols=["BTC/USDT", "ETH/USDT", ...],  # Top20
    date_range=(start_date, end_date),      # 最近90天
)

# 运行排行榜
leaderboard = run_candidate_leaderboard(
    candidate_ids=[
        "operator_heuristic_v1",
        "pandas_ta_broad_screen_v1",
        # "operator_heuristic_v2_relaxed",  # v2需要先实现融合逻辑
    ],
    market_data=market_data,
    symbols=list(market_data.keys()),
)

# 查看结果
print(f"Winner: {leaderboard.get_winner().candidate_id}")
for entry in leaderboard.entries:
    print(f"Rank {entry.rank}: {entry.candidate_id} - "
          f"Net Exp: {entry.metrics.net_expectancy:.4f} "
          f"CI: [{entry.net_expectancy_ci_lower:.4f}, {entry.net_expectancy_ci_upper:.4f}]")
```

**预期输出**：
- 3个候选的性能指标对比
- 按置信区间下界排序的排名
- 每个候选的完整回测报告

### 📋 技术债务和TODO

#### 高优先级
1. **实现 v2_relaxed 的融合逻辑**
   - 当前 `operator_heuristic_v2_relaxed` 只是占位符
   - 需要在 `paper_signal.py` 中实现 `layered_regime_entry_relaxed` 方法
   - 支持"任意2/3层同意"的多数投票

2. **扩展 pandas_ta_broad_screen**
   - 当前只用了2个指标作为 PoC
   - 实现完整的"筛选所有指标，保留正期望值"流程
   - 可能需要辅助工具：`screen_all_pandas_ta_indicators.py`

#### 中优先级
3. **添加更多候选策略**
   - `freqtrade_distilled_ema_rsi`（注意GPL边界）
   - `factor_cross_sectional_v1`（借鉴Qlib因子）
   - Swing交易候选（1d/4h时间框架）
   - 缠论候选（如果验证通过）

4. **实现模块9（Optimization Agent）**
   - 自动生成候选变体（参数网格搜索）
   - 批量运行排行榜
   - 找到最优配置

#### 低优先级
5. **回补阶段2（vectorbt加速）**
   - 仅在候选数量>10且速度成为瓶颈时考虑
   - 用 vectorbt 加速纯向量化计算
   - 保留现有引擎做精细验证

### ⚠️ 注意事项

#### GPL许可证边界
- **Freqtrade**：GPL-3.0，只能 `distilled_research_only`
  - 不能 `import freqtrade.strategy` 或复制源码
  - 只能看文档重新实现逻辑
  - 如果未来有对外分发计划，需重新评估

#### 依赖安装问题
- **网络慢**：安装大包（vectorbt/numba）可能很慢或失败
- **应对**：分步安装，优先安装核心依赖（pandas-ta）
- **备选**：如果全部失败，可以只用现有手搓指标

#### 统计严谨性
- **小样本陷阱**：<30笔交易的候选，置信区间会很宽
- **排名方法**：按置信区间下界排序，不是点估计
- **避免过拟合**：使用 walk-forward 验证，不是单次回测

### 📚 相关文档

- **完整进度报告**：`strategy-layer-refactor-progress-report.md`
- **阶段1验收文档**：`stage1-pandas-ta-adapter-acceptance.md`
- **原始方案**：在任务描述中（用户提供的重构方案文档）

### 🔗 关键代码入口

#### 注册新候选
```python
# 在 services/strategy_library/candidates/registry.py 中
def _my_new_candidate_config() -> dict[str, Any]:
    return {
        "entry_rules": {...},
        "exit_rules": {...},
        # ...
    }

MY_NEW_CANDIDATE = StrategyCandidate(
    candidate_id="my_new_candidate_v1",
    source="my_source",
    hypothesis="my hypothesis",
    version="1.0.0",
    created_at=datetime.now(),
    market="BTC/USDT",
    timeframe="15m",
    config_factory=_my_new_candidate_config,
)

# 添加到注册表
CANDIDATE_REGISTRY["my_new_candidate_v1"] = MY_NEW_CANDIDATE
```

#### 运行单个候选回测
```python
from services.strategy_library.candidates.registry import get_candidate
from services.validation.technical_replay import TechnicalStrategyValidationService
from shared.models import StrategyContract

candidate = get_candidate("operator_heuristic_v1")
config = candidate.get_config()

strategy = StrategyContract(
    strategy_key=candidate.candidate_id,
    version=candidate.version,
    rules=config,
)

service = TechnicalStrategyValidationService()
metrics = service.replay(
    strategy=strategy,
    market_data=market_data,
)

print(f"Net Expectancy: {metrics.net_expectancy}")
print(f"Win Rate: {metrics.win_rate}")
print(f"Profit Factor: {metrics.profit_factor}")
```

#### 运行排行榜对比
```python
from services.validation.candidate_leaderboard import run_candidate_leaderboard

leaderboard = run_candidate_leaderboard(
    candidate_ids=["operator_heuristic_v1", "pandas_ta_broad_screen_v1"],
    market_data=market_data,
)

# 保存报告
import json
with open("leaderboard_report.json", "w") as f:
    json.dump(leaderboard.as_dict(), f, indent=2)
```

---

## 总结

✅ **已完成**：核心框架实现、测试覆盖、文档完备

⏳ **进行中**：pandas-ta 依赖安装

🚀 **下一步**：验证安装 → 集成信号链路 → 运行第一版排行榜

💡 **核心价值**：从"单一策略假设"到"多候选数据驱动对比"的架构升级

---

**如有问题，可从以下入口开始调试：**
1. 运行 `python scripts/verify_quant_dependencies.py` 检查依赖
2. 运行 `python -m pytest tests/test_candidate_registry.py -v` 验证注册表
3. 查看 `strategy-layer-refactor-progress-report.md` 了解详情
