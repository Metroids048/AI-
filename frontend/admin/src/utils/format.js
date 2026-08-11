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

export function formatFieldLabel(value) {
  return FIELD_LABELS[String(value)] ?? String(value ?? "");
}

export function formatBoolean(value) {
  return value === true ? "是" : value === false ? "否" : "状态待确认";
}
