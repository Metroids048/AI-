"""C+ Weighted Ensemble strategy configuration.

C_PLUS_WEIGHTED_ENSEMBLE - 加权Ensemble策略版本
不再要求信号严格一致，通过市场状态路由和加权合成提高召回率。
"""

# C+ 策略版本标识
C_PLUS_STRATEGY_KEY = "c_plus_weighted_ensemble"
C_PLUS_STRATEGY_VERSION = "1.0.0"

# 策略规则定义
C_PLUS_STRATEGY_RULES = {
    "entry_rules": {
        "strategy_type": "c_plus_weighted",
        "fusion_method": "weighted_ensemble",  # 新融合方法
        "order_type": "MARKET",  # 默认市价单，Entry模型可覆盖
        "regime_awareness": True,  # 启用市场状态识别
        "strategy_family_routing": True,  # 启用策略族路由
        "long_threshold": 0.35,  # 加权分数阈值
        "short_threshold": -0.35,
        "min_total_weight": 0.5,  # 最小总权重
        # 技术信号定义（继承现有）
        "technical_signals": [
            "technical_ema_trend",
            "technical_dow_trend",
            "technical_mtf_ma",
            "technical_macd",
            "technical_adx",
            "technical_fvg",
            "technical_rsi",
            "technical_bollinger",
            "technical_vwap",
        ],
        # 成本建模（对齐真实费率）
        "core_fee_bps": 2.0,  # Binance USDM maker 2bps
        "core_slippage_bps": 1.0,
        "standard_fee_bps": 5.0,  # taker 5bps
        "standard_slippage_bps": 3.0,
        "minimum_net_reward_r": 1.0,  # 扣除成本后最小预期R
    },
    "exit_rules": {
        "close_on_opposite_signal": False,  # 不再简单反向平仓
        "strategy_family_specific": True,  # 使用策略族专属出场
        "time_exit_enabled": True,
        "trailing_stop_enabled": True,  # 启用追踪止损
    },
    "stoploss_rules": {
        "strategy_family_specific": True,  # 策略族专属止损
        "atr_based": True,
        # 默认参数，策略族可覆盖
        "default_atr_multiple": 2.0,
    },
    "takeprofit_rules": {
        "strategy_family_specific": True,  # 策略族专属止盈
        "partial_exit_enabled": True,  # 启用分批止盈
        # 默认参数
        "default_target_1_r": 1.5,
        "default_target_2_r": 2.5,
    },
    "position_rules": {
        # Paper测试采样档（与AUTO_PAPER_TECHNICAL_RULES对齐）
        "risk_per_trade": 0.05,  # 单笔风险5%
        "max_portfolio_initial_risk_fraction": 0.25,  # 组合初始风险上限25%
        "max_positions": 3,  # 最大持仓数
        "position_sizing_policy": "risk_based",  # 基于风险的仓位计算
    },
}

# 信号通道 / 决策阶段标识（配置用字符串，非 playbook Pydantic 模型枚举）
C_PLUS_SIGNAL_CHANNELS = [
    "technical_momentum",
    "technical_trend",
    "technical_structure",
    "technical_volume",
]

C_PLUS_DECISION_STAGES = [
    "regime_identification",
    "strategy_family_selection",
    "weighted_ensemble",
    "entry_model",
    "exit_model",
    "risk_calculation",
    "gatekeeper",
]

# 风险配置
C_PLUS_RISK_PROFILE = {
    "profile_key": "c_plus_paper_aggressive",
    "max_positions": 3,
    "max_leverage": 10,  # 固定杠杆，暂不使用波动率自适应
    "max_correlation": 0.7,
    "risk_per_trade": 0.05,
    "max_portfolio_risk": 0.25,
    "circuit_breaker": {
        "max_daily_loss": 0.10,
        "max_consecutive_losses": 5,
        "cooldown_hours": 24,
    },
}

# 执行范围（Paper阶段）
C_PLUS_EXECUTION_SYMBOLS = ["BTC/USDT", "ETH/USDT"]
C_PLUS_TIMEFRAME = "15m"

# 验证要求
C_PLUS_VALIDATION_REQUIREMENTS = {
    "min_backtest_days": 180,
    "min_trades": 30,
    "min_sharpe": 1.0,
    "max_drawdown": 0.25,
    "min_profit_factor": 1.3,
    "time_series_split": True,  # 必须时间序列切分
    "walk_forward": False,  # 第一阶段暂不要求
}
