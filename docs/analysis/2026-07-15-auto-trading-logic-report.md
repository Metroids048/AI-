# 7x24小时自动开平单逻辑详细报告

**生成时间**: 2026-07-15  
**系统版本**: Phase 0 完成 + P1落地  
**报告目的**: 详细梳理当前币安模拟盘自动交易逻辑、策略配置、风控机制和修改方法

---

## 一、系统架构总览

### 1.1 自动交易闭环
```
RuntimeScheduler (每60秒)
    ↓
PaperRuntimeService.run_cycle()
    ↓
├─ 1. sync_paper_account_equity()         # 同步Testnet账户权益
├─ 2. _reconcile_local_positions()        # 核对持仓,关闭幽灵仓位
├─ 3. _protective_management_1m()         # 1m K线检查止损/止盈
├─ 4. 遍历Top20币种池
│   ├─ MarketDataHeartbeatService         # 数据新鲜度检查
│   ├─ PaperSignalGenerator               # 信号生成与订单构造
│   │   ↓
│   ├─ DecisionPipeline.evaluate()        # 核心决策流程
│   │   ├─ 生成8个技术信号
│   │   ├─ SignalEnsemble融合
│   │   ├─ MetaLabel二次过滤
│   │   └─ LLM Veto(可选)
│   │   ↓
│   └─ ExecutionGatekeeperService         # 22条风控门禁
│       └─ Binance Testnet执行
└─ 5. 复盘与事件管理
```

### 1.2 核心组件文件
| 文件 | 行数 | 职责 |
|------|------|------|
| `services/execution/paper_runtime.py` | 1814 | 自动化主循环调度器 |
| `services/execution/decision_pipeline.py` | 833 | 信号生成与融合引擎 |
| `services/execution/paper_signal.py` | 737 | 订单构造与仓位管理 |
| `services/execution/gatekeeper.py` | 282 | 22条风控门禁 |
| `services/execution/bootstrap.py` | 808 | 策略配置与初始化 |
| `services/data/heartbeat.py` | 86 | 数据新鲜度监控 |
| `services/data/repository.py` | 595 | OHLCV存储与查询 |

---

## 二、三大策略通道 (Strategy Lanes)

### 2.1 Directional Lane (方向性交易)
**策略Key**: `auto_paper_mature_templates`  
**核心逻辑**: 多时间框架技术指标融合

#### 信号生成器 (8个)
1. **MACD** (权重1.0): 12/26/9参数,金叉做多/死叉做空
2. **Dow Trend** (权重0.9): 道氏理论,高低点突破
3. **EMA Trend** (权重0.9): 快慢线交叉 (EMA12/EMA26)
4. **ADX** (权重0.85): 趋势强度>25时才入场
5. **RSI** (权重0.75): 超买(>70)做空/超卖(<30)做多
6. **VWAP** (权重0.75): 价格与成交量加权均价关系
7. **Bollinger** (权重0.75): 布林带突破与回归
8. **FVG** (Fair Value Gap, 权重0.7): 价格缺口回补

#### 多时间框架确认 (Critical!)
```python
direction_timeframe = "4h"     # 主趋势方向
state_timeframe = "1h"         # 市场状态确认
entry_timeframe = "15m"        # 具体入场时机
protection_timeframe = "1m"    # 止损/止盈保护
```

**入场条件**:
1. 4h和1h的direction_signals (dow_trend, ema_trend, adx, mtf_ma) 必须同向
2. 15m的entry_signals (macd, price_action, rsi, vwap, bollinger, fvg) 达到融合阈值
3. MetaLabel历史胜率 >= 50%
4. 净期望值 > 扣除成本后仍为正

#### 仓位管理
- **风险预算**: 单笔交易账户权益的2.5%
- **波动率定量**: `notional = risk_budget / stop_distance * reference_price`
- **杠杆上限**: 25x (BTC/ETH/SOL), 10x (其他)
- **单币种曝光上限**: 20%

#### 止损/止盈
- **止损**: ATR × 2.0 或 固定250bps
- **止盈**: 固定2R (风险回报比2:1)
- **时间止损**: 持仓24小时后,若未达0.5R则平仓

---

### 2.2 Carry Lane (资金费率套利)
**策略Key**: `auto_paper_btc_funding`  
**核心逻辑**: 做空高费率永续合约,同时做多现货对冲

#### 入场条件
1. **资金费率阈值**: funding_rate >= 0.5 bps/8h
2. **净边缘检查**: `净收益 = |funding_rate| - 2×(手续费+滑点) >= 5 bps`
3. **仅做多费率**: `requires_positive_funding = True` (不做负费率套利)

#### Delta对冲机制
```python
# 开仓时同时执行两笔订单
perp_order = {
    "symbol": "BTC/USDT:USDT",
    "side": "SHORT",
    "notional": 1000 USDT,
    "leverage": 15x
}

hedge_order = {
    "symbol": "BTC/USDT",  # 现货
    "side": "LONG",
    "notional": 1000 USDT,
    "is_spot": True
}
```

#### 费用假设 (已校准为真实Binance费率)
- **核心交易对** (BTC/ETH/SOL): 5bps taker, 1bps滑点
- **标准交易对**: 5bps taker, 3bps滑点
- **往返成本**: 2 × (fee + slippage) = 12-16 bps

**当前状态**: ❌ 未开单  
**原因**: 真实资金费率0.3-1 bps/8h,无法覆盖12bps往返成本

---

### 2.3 Cross-Sectional Carry (跨截面套利)
**策略Key**: `auto_paper_cross_sectional_carry`  
**核心逻辑**: 做空Top3高费率币种,做多Top3低费率币种

#### 排名机制
```python
def compute_funding_rank_snapshot():
    # 获取所有Top20的最新资金费率
    ranked = sorted(symbols, key=lambda s: get_funding_rate(s))
    
    long_basket = ranked[:3]   # 费率最低的3个(可能为负)
    short_basket = ranked[-3:]  # 费率最高的3个
    
    return {
        symbol: {
            "basket_side": "long_candidate" if in long_basket else "short_candidate",
            "funding_rate_bps": funding_rate * 10000,
            "rank": index + 1
        }
    }
```

#### 出场条件
- **排名滑出**: 当某币种不再位于Top3/Bottom3时自动平仓
- **再平衡**: 每8小时重新排名

**当前状态**: ❌ 研究候选,未启用  
**原因**: 历史回测显示17.4%胜率,-46.7%累计收益 (2026-07-13审计)

---

## 三、22条Gatekeeper风控规则

### 3.1 验证与数据门禁
1. `kill_switch_active`: 全局紧急停止开关
2. `missing_stoploss`: 无止损的订单直接拒绝
3. `missing_validation_run`: 未经回测验证的策略拒绝
4. `validation_gate_rejected`: 回测未通过门槛
5. `data_not_fresh`: K线数据超过2小时未更新

### 3.2 仓位与杠杆限制
6. `max_symbol_exposure_exceeded`: 单币种曝光 > 20%
7. `max_total_exposure_exceeded`: 总曝光 > 80%
8. `max_open_positions_exceeded`: 持仓数量 > 6
9. `max_leverage_exceeded`: 杠杆 > 资产等级上限
10. `single_trade_stop_risk_exceeded`: 单笔止损风险 > 2.5%

### 3.3 相关性风控 (Critical!)
11. `portfolio_correlation_unavailable`: 缺少60根1h K线无法计算相关性
12. `correlated_exposure_limit_exceeded`: >=2个同向高相关(>0.7)持仓
13. `correlated_cluster_exposure_exceeded`: 相关性簇总曝光 > 35%
14. `net_directional_exposure_exceeded`: 净多头或净空头曝光 > 40%

### 3.4 亏损保护
15. `daily_loss_limit_breached`: 当日亏损 >= 账户权益×5%
16. `weekly_loss_limit_breached`: 周亏损 >= 账户权益×10%
17. `hard_stop_drawdown_breached`: 回撤 >= 峰值×20%
18. `drawdown_limit_breached`: 回撤 >= 峰值×15%
19. `consecutive_loss_limit_breached`: 连续亏损 >= 3笔

### 3.5 风险事件与边缘
20. `blocking_risk_event`: 存在未解决的高严重性风险事件
21. `net_edge_after_cost_negative`: 扣除手续费后期望为负
22. `martingale_detected`: 加仓幅度>2×现有曝光 (防止马丁策略)

---

## 四、配置参数详解

### 4.1 策略配置 (bootstrap.py)

#### Directional策略核心参数
```python
AUTO_PAPER_TECHNICAL_RULES = {
    "entry_rules": {
        # 多时间框架模型
        "direction_timeframe": "4h",
        "state_timeframe": "1h", 
        "entry_timeframe": "15m",
        
        # 信号配置
        "direction_signals": ["dow_trend", "ema_trend", "adx", "mtf_ma"],
        "entry_signals": ["macd", "price_action", "rsi", "vwap", "bollinger", "fvg"],
        
        # MetaLabel阈值
        "meta_label_min_win_rate": 0.50,
        
        # 费用假设 (真实Binance费率)
        "core_fee_bps": 5.0,        # BTC/ETH/SOL taker费率
        "core_slippage_bps": 1.0,
        "standard_fee_bps": 5.0,
        "standard_slippage_bps": 3.0,
    },
    
    "stoploss_rules": {
        "atr_multiple": 2.0,
        "fixed_bps": 250
    },
    
    "takeprofit_rules": {
        "risk_reward": 2.0  # 固定2R止盈
    },
    
    "position_rules": {
        "risk_per_trade": 0.025,                      # 2.5%
        "max_portfolio_initial_risk_fraction": 0.15, # 15%
        "max_leverage": 25,                           # BTC/ETH/SOL
        "max_position_fraction": 0.20,                # 20%
        "min_notional_usdt": 20
    }
}
```

#### Carry策略核心参数
```python
AUTO_PAPER_STRATEGY_RULES = {
    "entry_rules": {
        "funding_threshold_bps": 0.5,          # 0.5 bps/8h
        "min_estimated_net_edge_bps": 5.0,     # 净边缘>=5bps
        "requires_positive_funding": True,     # 仅做多费率
        "fee_bps": 5.0,
        "slippage_bps": 3.0
    },
    
    "position_rules": {
        "risk_per_trade": 0.015,    # 1.5%
        "max_leverage": 15,
        "max_position_fraction": 0.18
    }
}
```

### 4.2 RiskProfile参数 (Medium档位)
```python
medium_risk_profile = {
    "max_symbol_exposure": 0.20,           # 单币种20%
    "max_total_exposure": 0.80,            # 总曝光80%
    "max_open_positions": 6,               # 最多6个仓位
    "max_leverage": 20.0,                  # 杠杆上限20x
    "single_trade_risk_limit": 0.025,      # 单笔止损风险2.5%
    "daily_loss_limit": 0.05,              # 日亏损5%
    "weekly_loss_limit": 0.10,             # 周亏损10%
    "drawdown_limit": 0.15,                # 回撤预警15%
    "hard_stop_drawdown_limit": 0.20,      # 回撤硬停20%
    "consecutive_loss_limit": 3            # 连续亏损3笔
}
```

### 4.3 运行时配置 (settings.py)
```python
# 数据新鲜度
market_data_stale_seconds = 7200        # 2小时
execution_freshness_delay_seconds = 7200

# LLM Veto
decision_veto_daily_budget = 100        # 每日100次LLM调用
decision_veto_enabled = True

# Binance Testnet
binance_use_testnet = True
binance_auto_execute = True             # 自动镜像到Testnet

# 调度器
runtime_scheduler_mode = "inprocess"    # API进程内调度
runtime_scheduler_autostart = True
runtime_scheduler_interval_seconds = 60 # 每分钟一次cycle
```

---

## 五、如何修改配置

### 5.1 调整策略参数
**文件**: `services/execution/bootstrap.py`

**常见修改**:
1. **降低风险**: `risk_per_trade: 0.025 → 0.01`
2. **放宽止损**: `atr_multiple: 2.0 → 2.5`
3. **提高杠杆**: `max_leverage: 25 → 30` (需同步修改RiskProfile)
4. **修改时间框架**: `entry_timeframe: "15m" → "5m"`

**修改后需要**:
1. 重启API服务: `cd apps/api && python local_server.py`
2. 观察日志: `tail -f logs/paper_runtime.log`
3. 检查拒绝原因: 查询`order_executions`表的`rejection_codes`字段

### 5.2 启用/禁用策略通道
**启用研究候选策略**:
```python
# 在 bootstrap_local_paper_runtime() 中取消注释
def bootstrap_local_paper_runtime():
    bootstrap_auto_trading_paper_run()           # Carry lane
    bootstrap_auto_trading_technical_paper_run() # Directional lane
    # bootstrap_auto_trading_swing_paper_run()   # 取消注释启用Swing
```

**禁用LLM Veto**:
```python
# 方法1: 修改settings.py
decision_veto_enabled = False

# 方法2: 在PaperRun的execution_profile中设置
paper_run.execution_profile["llm_veto_enabled"] = False
```

### 5.3 修改Top20币种池
**文件**: `services/data/service.py`
```python
DEFAULT_BINANCE_TOP20 = [
    "BTC/USDT", "ETH/USDT", "SOL/USDT",
    # ... 根据需要增删
]
```

**注意**: 修改后需重新运行`bootstrap_seed_multi_timeframe_ohlcv()`

---

## 六、监控与调试

### 6.1 关键日志位置
- **Paper Cycle日志**: `logs/paper_runtime.log`
- **数据拉取日志**: `logs/data_ingestion.log`
- **LLM Veto日志**: `logs/agents.log`

### 6.2 数据库查询调试

**查看最近的拒绝原因**:
```sql
SELECT symbol, rejection_reason, rejection_codes, created_at
FROM order_executions
WHERE execution_status = 'rejected'
ORDER BY created_at DESC
LIMIT 20;
```

**查看持仓状态**:
```sql
SELECT symbol, side, quantity, entry_price, mark_price, 
       (mark_price - entry_price) * quantity AS unrealized_pnl
FROM position_snapshots
WHERE run_type = 'paper'
  AND ABS(quantity) > 0
ORDER BY snapshot_time DESC;
```

**查看账户权益历史**:
```sql
SELECT paper_run_id, 
       paper_metrics_summary->>'account_equity' AS equity,
       paper_metrics_summary->>'net_realized_pnl_total' AS realized_pnl,
       paper_metrics_summary->>'last_cycle_at' AS last_cycle
FROM paper_runs
WHERE paper_status = 'running';
```

### 6.3 API端点调试

**手动触发一次Paper Cycle**:
```bash
curl -X POST http://localhost:8016/api/v1/console/trigger-paper-cycle \
  -H "Authorization: Bearer ${ADMIN_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "paper_run_id": "your-paper-run-id",
    "max_symbols": 5,
    "enable_decision_veto": false
  }'
```

**查看Paper Run状态**:
```bash
curl http://localhost:8016/api/v1/console/paper-status/your-paper-run-id \
  -H "Authorization: Bearer ${ADMIN_TOKEN}"
```

---

## 七、常见问题排查

### Q1: 为什么长时间没有开单?

**诊断步骤**:
1. 检查`decision_snapshots`表,查看`pipeline_status`分布:
   ```sql
   SELECT pipeline_status, COUNT(*) 
   FROM decision_snapshots 
   WHERE cycle_time > NOW() - INTERVAL '24 hours'
   GROUP BY pipeline_status;
   ```

2. 常见拒绝原因:
   - `technical_signals_insufficient`: 8个指标未达到融合阈值
   - `multi_timeframe_disagreement`: 4h和15m方向不一致
   - `meta_label_bet_skipped`: 历史胜率<50%
   - `net_edge_after_cost_negative`: 扣除成本后期望为负

3. 如果所有拒绝都是`net_edge_after_cost_negative`,说明**策略本身缺乏盈利能力**,这是正确行为,不应放宽门槛

### Q2: 止损频繁触发怎么办?

**可能原因**:
1. 止损距离太小: 增大`atr_multiple`从2.0到2.5
2. 市场波动过大: 检查`volatility_regime`是否为`high`
3. 入场时机不佳: 优化`entry_signals`组合

**不建议**:
- ❌ 取消止损 (违反AGENTS.md强制规则)
- ❌ 扩大止损到5R+ (会导致单笔风险过高触发`single_trade_stop_risk_exceeded`)

### Q3: 如何提高开单频率?

**合理方法**:
1. **降低MetaLabel阈值**: `meta_label_min_win_rate: 0.50 → 0.45`
2. **增加信号源**: 启用更多技术指标
3. **缩短时间框架**: `entry_timeframe: "15m" → "5m"` (会增加噪音)
4. **扩大币种池**: 从Top20扩展到Top50

**不合理方法** (违反AGENTS.md):
- ❌ 放宽`net_edge_after_cost`门槛 (会允许负期望交易)
- ❌ 跳过multi_timeframe确认 (会增加假信号)
- ❌ 禁用相关性风控 (会导致过度集中风险)

---

## 八、为什么当前没有开单? (回答用户问题3)

### 8.1 根本原因诊断
根据2026-07-13和2026-07-14的真实数据审计(TASK-059, ADR-063):

**三种策略形态的实际表现**:
1. **Directional (方向性)**: 
   - OOS净期望: -0.23%
   - 结论: 8指标融合后扣除6bps成本仍为负

2. **Single-Symbol Carry (单币种资金费率)**: 
   - 真实资金费率: 0.3-1 bps/8h
   - 往返成本: 12bps
   - 结论: 收益无法覆盖成本

3. **Cross-Sectional Carry (跨截面套利)**: 
   - 历史胜率: 17.4%
   - 累计收益: -46.7%
   - 结论: 未启用,研究候选阶段

### 8.2 系统行为判定
✅ **代码正常运行**: 
- RuntimeScheduler每60秒正常触发
- 数据新鲜度检查通过
- 信号生成流程完整
- Gatekeeper正确执行22条规则

❌ **策略缺乏正期望**:
- `net_edge_after_cost_negative`规则正确拒绝负期望交易
- 这是fail-closed设计的预期行为
- **不是Bug,是风控正确工作**

### 8.3 结论
**当前没有开单是因为系统正确地拒绝了所有负期望的交易机会。**

根据AGENTS.md第5条非协商原则: "风控优先级永远高于收益"，不应为了"开单"而放宽`net_edge_after_cost`门槛。

---

## 九、下一步建议

### 9.1 策略优化方向
1. **开发新策略形态**: 
   - 1d/4h中期Swing策略 (已注册未验证)
   - 波动率突破策略
   - 流动性挖矿策略

2. **优化现有信号组合**:
   - 使用`scripts/compute_signal_edge_stats.py`分析哪些信号组合有正边缘
   - 调整信号权重分配
   - 探索不同时间框架组合

3. **扩展数据源**:
   - 引入链上数据 (巨鲸转账、交易所流入流出)
   - 宏观事件日历 (FOMC、CPI)
   - 社媒情绪分析

### 9.2 不应采取的行动
❌ **放宽成本门槛**: 保持`net_edge_after_cost`规则,避免长期亏损  
❌ **跳过验证层**: 新策略必须先通过历史回测  
❌ **取消止损规则**: 违反AGENTS.md强制要求  
❌ **人工干预开单**: 破坏系统化交易原则

### 9.3 监控建议
1. **每日查看拒绝原因分布**:
   ```sql
   SELECT rejection_codes, COUNT(*) 
   FROM order_executions 
   WHERE created_at > NOW() - INTERVAL '1 day'
   GROUP BY rejection_codes;
   ```

2. **定期审计策略有效性**: 每周运行`audit_signal_edge_stats.py`

3. **保持记忆文件更新**: 重要发现记录到`decisions-log.md`

---

## 十、总结

### 10.1 当前状态
✅ **机械执行**: 完整,7x24稳定运行  
✅ **风控体系**: 22条门禁,fail-closed设计  
✅ **数据完整**: Top20 OHLCV、资金费率、技术指标齐全  
✅ **Binance对接**: Testnet镜像执行正常  
❌ **盈利能力**: 三种策略形态均未通过成本门槛

### 10.2 核心认知
这是一个**AI驱动的量化研究平台**,不是"AI帮我赚钱"的荐股工具。当前"不开单"说明:
1. 风控系统正确工作
2. 策略研究尚未找到正期望形态
3. 需要持续迭代优化策略库

### 10.3 修改配置的原则
- **可以改**: 时间框架、指标权重、MetaLabel阈值、币种池
- **谨慎改**: 止损距离、杠杆上限、仓位比例
- **不能改**: 成本门槛、止损强制要求、相关性风控逻辑

---

**报告作者**: Claude (Kiro AI Development Environment)  
**参考文档**: 
- `.github/agent/memory/decisions-log.md` (ADR-058至ADR-064)
- `.github/agent/memory/project-memory.md`
- `.github/agent/memory/task-history.md` (TASK-059, TASK-061)
- `AGENTS.md` (项目宪章)
