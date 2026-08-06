# Iteration 2 → 方案 A+C 实施交接文档

## 当前状态（2026-08-06）

**Iteration 2 诊断已完成**，根因已锁定，需要立即执行方案 A+C 组合修复。

## 根因总结（三层证据链）

1. **Regime 分布极度失衡**
   - 最近7天 2556 条 ensemble 记录
   - RANGE: 2449 条 (95.8%)
   - TREND_UP/DOWN: 91 条 (3.6%)
   - UNCERTAIN: 16 条 (0.6%)

2. **过滤规则过严**
   - `services/strategy_library/ensemble/service.py:327-328`
   - RANGE regime 硬性要求 `signal_layer == "range"` 才能投票
   - 其他层信号全部被过滤

3. **信号池结构性不匹配**
   - RANGE 层只有 2 个策略（vwap + bollinger）
   - 7天触发 542 次，占总触发 7518 次的 7.2%
   - 被过滤的策略：technical_ema_trend (11.4%), market_intelligence (25.6%)

**直接后果**：1921/2449 (78.4%) 的 RANGE ensemble 因 `eligible_count=0` 被丢弃

## 方案 A+C：组合修复

### 改动 1：放宽 RANGE regime 融合规则

**文件**：`services/strategy_library/ensemble/service.py`

**位置**：L327-328 附近的 `_eligible_layered_signals` 逻辑

**当前代码**：
```python
if regime == MarketRegime.RANGE:
    eligible = [sig for sig in signals if sig.signal_layer == "range"]
```

**修改为**：
```python
if regime == MarketRegime.RANGE:
    # TEMP_FIX_GATE17_2026_08_06: Allow entry layer for signal scarcity
    # TODO: Replace with expanded RANGE strategy pool (Plan B, 1-2 weeks)
    eligible = [sig for sig in signals
                if sig.signal_layer in ("range", "entry")]
    # Still filter out "direction" layer (trend-following strategies)
```

**预期效果**：
- `eligible_count=0` 的 RANGE ensemble 占比从 78.4% 降到 < 30%
- macd, price_action 等 entry 层策略可以在 RANGE regime 下投票

---

### 改动 2：调整 regime 判定阈值

**任务**：先找到 `market_regime_router` 的代码位置

**预期位置**：
- `services/strategy_library/regime/`
- 或 `services/strategy_library/router/`
- 或 `shared/models/regime.py` 相关逻辑

**需要执行**：
```bash
grep -r "MarketRegime.RANGE\|regime.*RANGE\|ADX.*<" services/strategy_library/ shared/
```

**目标修改**：
- 找到判定 RANGE 的条件（预期是 `ADX < 20` 或类似阈值）
- 收紧为 `ADX < 15` 或更严格的条件
- 目标：让 RANGE 占比从 95.8% 降到 60-70%

**同样打标记**：
```python
# TEMP_FIX_GATE17_2026_08_06: Tighten RANGE threshold for signal pool balance
# TODO: Re-evaluate after Plan B expands RANGE strategy pool
```

---

## 验收清单（改动完成后必须执行）

### 1. 代码自查
```bash
# 读取修改后的关键函数，确认逻辑已落地
Read services/strategy_library/ensemble/service.py:320-340
Read <regime_router_file>:<修改的行号前后10行>
```

### 2. 测试验证
```bash
# 运行 ensemble 相关测试
pytest tests/services/strategy_library/ensemble/ -v

# 运行 regime 相关测试（如果存在）
pytest tests/services/strategy_library/ -k regime -v

# 全量回归（确保没有破坏其他逻辑）
pytest tests/services/ -q
```

### 3. 配置同步
```bash
# 确认新配置已写入数据库（如果 regime 阈值存在数据库）
python scripts/verify_runtime_config_sync.py
```

### 4. 重启系统
```bash
# 完全重启（停止旧进程）
完全重启系统.cmd

# 等待启动完成，确认日志无报错
tail -f logs/v2-runtime-watch.jsonl
```

### 5. 运行时验证（重启后 2-4 小时）
```bash
# 重跑决策漏斗
python -m scripts.audit_decision_funnel --database-url sqlite:///.local_paper_console.db --lookback-days 1

# 统计新的 regime 分布
python -c "
import sqlite3
conn = sqlite3.connect('.local_paper_console.db')
cursor = conn.cursor()
cursor.execute('''
    SELECT
        CASE
            WHEN correlation_matrix_ref LIKE \"%'market_regime': 'range'%\" THEN 'range'
            WHEN correlation_matrix_ref LIKE \"%'market_regime': 'trend_up'%\" THEN 'trend_up'
            WHEN correlation_matrix_ref LIKE \"%'market_regime': 'trend_down'%\" THEN 'trend_down'
            ELSE 'other'
        END as regime,
        COUNT(*) as cnt
    FROM signal_ensembles
    WHERE created_at > datetime('now', '-2 hours')
    GROUP BY regime
''')
print('最近2小时 regime 分布:')
for regime, cnt in cursor.fetchall():
    print(f'  {regime:15s} {cnt:5d}')
conn.close()
"

# 统计 RANGE ensemble 的 eligible_count=0 占比
python -c "
import sqlite3
conn = sqlite3.connect('.local_paper_console.db')
cursor = conn.cursor()
cursor.execute('''
    SELECT
        CASE
            WHEN correlation_matrix_ref LIKE \"%'eligible_count': 0%\" THEN 'eligible=0'
            ELSE 'eligible>0'
        END as status,
        COUNT(*) as cnt
    FROM signal_ensembles
    WHERE created_at > datetime('now', '-2 hours')
    AND correlation_matrix_ref LIKE \"%'market_regime': 'range'%\"
    GROUP BY status
''')
print('最近2小时 RANGE ensemble eligible 分布:')
for status, cnt in cursor.fetchall():
    print(f'  {status:15s} {cnt:5d}')
conn.close()
"
```

**目标指标**：
- RANGE regime 占比：从 95.8% → 60-70%
- RANGE ensemble `eligible_count=0` 占比：从 78.4% → < 30%

---

## 账本更新（验证通过后）

在 `docs/loop/GATE17_LOOP_LEDGER.yaml` 的 `iteration: 2` 条目中更新：

```yaml
verdict: "CONFIRMED → FIXED_TEMP"
action_taken: "CODE_CHANGE — relaxed ensemble filtering + tightened regime threshold"
files_touched:
  - services/strategy_library/ensemble/service.py
  - <regime_router_file>
locked: false  # 保持 false，因为这是临时修复，方案 B 完成后需要回滚
temp_fix_note: |
  这是临时修复，为方案 B（扩充 RANGE 策略池）争取时间。
  当 RANGE 层策略触发密度 > 20% 时，必须回滚此修改并恢复严格分层过滤。
  回滚时机预计：2026-08-20 前后（方案 B 预计 1-2 周完成）
```

---

## 下一步（新会话启动方案 B）

方案 A+C 验证通过后，启动方案 B（扩充信号池）：

1. **Mean Reversion 策略**（优先级 1）
   - 基于 Bollinger 回归 + VWAP 偏离
   - 目标：2-3 天完成开发 + 回测

2. **Support/Resistance Bounce**（优先级 2）
   - 基于历史高低点 + 整数关口
   - 目标：3-4 天完成开发 + 回测

3. **Donchian/Keltner Channel**（优先级 3）
   - 补充通道类指标
   - 目标：1-2 天完成开发 + 回测

**方案 B 验收标准**：
- 每个新策略 Sharpe > 1.0, OOS > 50 笔, Expectancy > 0
- RANGE 层触发密度 > 20%（当前 7.2%）
- 在 RANGE regime 样本上单独验证

---

## 引用本文档的方式（新会话）

在新会话中直接说：

```
我要执行 Gate 17 修复的方案 A+C，诊断结果和具体改动已经在
docs/loop/ITERATION2_HANDOFF.md 中。

请阅读该文件，然后：
1. 执行改动 1 和改动 2
2. 运行验收清单中的所有步骤
3. 更新账本

完成后给我完整的验收报告。
```

---

## 当前账本路径

`docs/loop/GATE17_LOOP_LEDGER.yaml`

最新状态：
- Iteration 1: VERIFIED (Gatekeeper 配置问题已修复)
- Iteration 2: CONFIRMED (根因已锁定，待执行 A+C)
