export function formatNumber(value, digits = 2) {
  if (value === null || value === undefined || value === "") return "--";
  const number = Number(value);
  if (!Number.isFinite(number)) return "--";
  return new Intl.NumberFormat("zh-CN", {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  }).format(number);
}

export function formatCompact(value, digits = 2) {
  if (value === null || value === undefined || value === "") return "--";
  const number = Number(value);
  if (!Number.isFinite(number)) return "--";
  return new Intl.NumberFormat("zh-CN", {
    notation: "compact",
    maximumFractionDigits: digits,
  }).format(number);
}

export function formatPercent(value) {
  if (value === null || value === undefined || value === "") return "--";
  const number = Number(value);
  if (!Number.isFinite(number)) return "--";
  return `${(number * 100).toFixed(4)}%`;
}

export function formatTime(value) {
  if (!value) return "--";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "--";
  return date.toLocaleString("zh-CN", { hour12: false, timeZone: "Asia/Shanghai" });
}

export function formatClock(value) {
  if (!value) return "--";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "--";
  return date.toLocaleTimeString("zh-CN", { hour12: false, timeZone: "Asia/Shanghai" });
}

export function asArray(value) {
  return Array.isArray(value) ? value : [];
}

const ENUM_LABELS = {
  pending: "待处理",
  healthy: "正常",
  degraded: "异常/降级",
  configured: "已配置",
  missing: "未配置",
  not_probed: "未探测",
  long: "做多",
  short: "做空",
  ACTIVE: "已启用",
  BINANCE_TESTNET: "币安模拟盘",
  grid_search: "网格搜索",
  hyperopt: "Hyperopt",
  in_progress: "进行中",
  done: "已完成",
  indexed_only: "仅已收录",
  registered: "已登记",
  none: "无",
  unknown: "状态待确认",
};

const FIELD_LABELS = {
  execution_engine: "执行引擎",
  optimization_method: "优化方式",
  implementation_status: "接入状态",
  scheduler_error: "调度器异常",
  next_cycle_eta_seconds: "距下次执行（秒）",
  risk_profile: "风控配置",
  operator_risk_per_trade: "单笔风险比例",
  generic_risk_profile_max_leverage: "默认最大杠杆",
  queue_backlog: "队列积压",
  last_auto_cycle_at: "最近自动循环",
  execution_scope_coverage: "执行范围覆盖",
  execution_symbols: "执行标的",
  acceptance_scope: "验收范围",
  last_strategy_gateway_order: "最近策略网关订单",
};

export function formatEnum(value, fallback = "状态待确认") {
  if (value === true) return "是";
  if (value === false) return "否";
  if (value === null || value === undefined || value === "") return fallback;
  return ENUM_LABELS[String(value)] ?? String(value);
}

/**
 * Decision funnel stages (shared/models/execution_truth.py DecisionFunnelStage).
 * These are the pipeline checkpoints a decision bar passes through.
 */
const DECISION_STAGE_LABELS = {
  data_available: "数据就绪",
  data_fresh: "数据新鲜度",
  regime_confirmed: "市场状态确认",
  entry_signal: "入场信号",
  candidate_created: "生成候选",
  meta_label_passed: "元标签筛选",
  manifest_eligible: "策略清单准入",
  reconciliation_healthy: "对账健康",
  risk_approved: "风控通过",
  ai_reviewed: "AI 复核",
  price_drift_passed: "价格漂移检查",
  exchange_submitted: "已提交交易所",
  exchange_filled: "交易所成交",
  protection_confirmed: "止损止盈已挂",
};

/**
 * Terminal reason codes. Only codes whose meaning was confirmed in the backend are
 * mapped; anything else falls through to the raw code so a new backend reason is
 * visibly untranslated instead of silently mislabeled.
 */
const DECISION_REASON_LABELS = {
  // Observed in the running console
  ENSEMBLE_DISCARDED: "多策略投票未形成方向",
  TESTNET_SAMPLING_SIGNAL: "采样通道信号",
  TECHNICAL_SIGNALS_INSUFFICIENT: "技术信号不足",
  POSITION_MANAGEMENT_ONLY: "仅持仓管理，不开新仓",
  SAMPLING_RULES_NOT_ALIGNED: "采样规则未对齐",
  VETOED: "决策被否决",
  BET_TAKEN: "已下注",
  PROTECTION_CONFIRMED: "止损止盈已确认",
  // Entry / signal
  NO_ENTRY_SIGNAL: "无入场信号",
  NO_CLOSED_CANDLE: "K 线未收盘",
  INSUFFICIENT_HISTORY: "历史数据不足",
  EMA_DIRECTION_MISMATCH: "EMA 方向不一致",
  MACD_DIRECTION_MISMATCH: "MACD 方向不一致",
  RSI_OUTSIDE_RANGE: "RSI 超出区间",
  ATR_NOT_POSITIVE: "ATR 非正值",
  MULTI_TIMEFRAME_DISAGREEMENT: "多周期方向冲突",
  FOUR_HOUR_DIRECTION_CONFLICT: "4 小时方向冲突",
  ONE_HOUR_REGIME_RANGE: "1 小时处于震荡区间",
  SINGLE_TIMEFRAME_LANE: "单周期通道",
  SIGNAL_CONFIDENCE_BELOW_THRESHOLD: "信号置信度低于阈值",
  REGIME_NOT_ELIGIBLE: "市场状态不符合准入",
  // Meta label / manifest
  META_LABEL_BET_SKIPPED: "元标签判定跳过",
  META_LABEL_NOT_CONFIGURED: "元标签未配置",
  MANIFEST_NOT_ELIGIBLE: "策略清单不准入",
  MANIFEST_UNAVAILABLE: "策略清单不可用",
  // Risk / gates
  NET_EDGE_AFTER_COST_NEGATIVE: "扣除成本后预期为负",
  NO_TRADE_COST_INEFFICIENT: "成本导致净盈亏比不足",
  RISK_LIMIT_EXCEEDED: "超出风控限额",
  PRICE_DRIFT_EXCEEDED: "价格漂移超限",
  DAILY_TRADE_LIMIT_REACHED: "已达当日交易上限",
  SYMBOL_COOLDOWN_ACTIVE: "该标的处于冷却期",
  POSITION_ALREADY_OPEN: "已有持仓",
  DUPLICATE_DECISION: "重复决策",
  CANDIDATE_EXPIRED: "候选已过期",
  CANDIDATE_CONSTRUCTION_FAILED: "候选构建失败",
  ENTRY_KILL_SWITCH_ACTIVE: "入场熔断已触发",
  SYMBOL_NOT_IN_EXECUTION_UNIVERSE: "标的不在执行范围内",
  UNMANAGED_EXTERNAL_POSITION: "存在未托管的外部持仓",
  MANUAL_POSITION_DIRECTION_CONFLICT: "手动持仓方向冲突（仅拒绝本笔）",
  // Availability
  EXCHANGE_UNAVAILABLE: "交易所不可用",
  EXCHANGE_UNKNOWN: "交易所状态未知",
  EXCHANGE_REJECTED: "交易所拒单",
  MARKET_DATA_UNAVAILABLE: "行情数据不可用",
  MARKET_DATA_STALE: "行情数据过期",
  NO_MARKET_DATA: "无行情数据",
  MARKET_RULES_UNAVAILABLE: "交易规则不可用",
  LOCAL_STATE_UNAVAILABLE: "本地状态不可用",
  RECONCILIATION_UNAVAILABLE: "对账不可用",
  RECONCILIATION_DEGRADED: "对账降级",
  RECOVERY_REQUIRED: "需要恢复处理",
  AI_PROVIDER_UNAVAILABLE: "AI 服务不可用",
  AI_REVIEW_DISABLED: "AI 复核未启用",
  AI_ADVISORY_VETO: "AI 建议否决",
  SHADOW_MODE_NO_SUBMIT: "影子模式，不提交订单",
  INTERNAL_ERROR: "内部错误",
  // Success path
  OK: "正常",
  APPROVED: "已通过",
  ENTRY_INTENT_CREATED: "已生成入场意图",
  CANDIDATE_ACCEPTED: "候选已接受",
  candidate_accepted: "候选已接受",
  EXCHANGE_FILLED: "交易所已成交",
  ACKNOWLEDGED_UNFILLED: "已受理未成交",
  PARTIALLY_FILLED: "部分成交",
  FILLED: "已成交",
  PROTECTION_FAILED: "止损止盈挂单失败",
};

export function formatDecisionStage(value) {
  if (value === null || value === undefined || value === "") return "--";
  const key = String(value);
  return DECISION_STAGE_LABELS[key] ?? DECISION_STAGE_LABELS[key.toLowerCase()] ?? key;
}

export function formatDecisionReason(value) {
  if (value === null || value === undefined || value === "") return "--";
  const key = String(value);
  return DECISION_REASON_LABELS[key] ?? DECISION_REASON_LABELS[key.toUpperCase()] ?? key;
}

/**
 * Translate the deterministic Runtime Truth no-trade projection for the
 * homepage. Raw codes remain available in the operations view and tooltips.
 */
export function formatNoTradeSummary(summaryCode, dominantReason, count = 0) {
  const reason = dominantReason ? formatDecisionReason(dominantReason) : "当前条件";
  const suffix = Number(count) > 0 ? `（${Number(count)} 次）` : "";
  const labels = {
    SCHEDULER_OFFLINE: "自动交易程序异常：调度器心跳中断",
    EXCHANGE_UNAVAILABLE: "自动交易程序异常：交易所暂不可用",
    MARKET_DATA_STALE: "自动交易程序异常：行情数据未及时更新",
    RECONCILIATION_BLOCKED: "自动交易程序异常：账户对账异常",
    ENTRY_PAUSED: `新开仓已暂停：${reason}`,
    DECISION_PIPELINE_STALLED: "自动交易程序异常：策略判断流水已停止更新",
    ENTRY_BLOCKED: `新开仓被拦截：${reason}${suffix}`,
    HEALTHY_WAITING_FOR_SIGNAL: "策略正在等待交易机会",
  };
  return labels[summaryCode] ?? "运行状态待确认";
}

/** Strip the CCXT perpetual suffix so the UI shows BTC/USDT, not BTC/USDT:USDT. */
export function formatSymbol(value) {
  if (value === null || value === undefined || value === "") return "--";
  return String(value).replace(":USDT", "");
}

const RUNTIME_ERROR_LABELS = [
  [/exchange truth probe exceeded/i, "交易所账户读取超时，正在后台重试"],
  [/exchange truth probe already in progress/i, "交易所账户读取中，请稍候"],
  [/exchange snapshot omitted open_positions/i, "交易所返回数据不完整"],
  [/timed? ?out|timeout/i, "请求超时"],
  [/connection|network|unreachable|refused/i, "网络连接失败"],
];

/** Turn a backend technical error into operator-facing Chinese, keeping the raw text as detail. */
export function formatRuntimeError(value) {
  if (!value) return "";
  const text = String(value);
  for (const [pattern, label] of RUNTIME_ERROR_LABELS) {
    if (pattern.test(text)) return label;
  }
  return text;
}

export function formatFieldLabel(value) {
  return FIELD_LABELS[String(value)] ?? String(value ?? "");
}

export function formatBoolean(value) {
  return value === true ? "是" : value === false ? "否" : "状态待确认";
}
