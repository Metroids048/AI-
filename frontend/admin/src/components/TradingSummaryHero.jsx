/**
 * 交易总览 Hero - 主页核心信息卡片
 * 展示账户、持仓、盈亏、策略状态等关键指标
 */
import { formatDecisionReason, formatNoTradeSummary, formatNumber, formatRuntimeError } from "../utils/format";

const DEFAULT_BINANCE_TESTNET_URL = "https://testnet.binancefuture.com/en/futures/BTCUSDT";

export function TradingSummaryHero({
  account,
  positions,
  orders,
  decisions,
  noTradeSummary,
  tradingStatus,
  globalRiskStatus,
  streamStatus,
  selectedSymbol,
  lastSuccessAt,
}) {
  const positionCount = Array.isArray(positions) ? positions.length : null;
  const orderCount = Array.isArray(orders) ? orders.length : null;

  // 计算浮动盈亏
  const accountAvailable = account?.status === "available" || account?.connected === true;
  const unrealizedPnl = accountAvailable && account?.unrealized_pnl != null
    ? Number(account.unrealized_pnl)
    : Array.isArray(positions)
      ? positions.reduce((sum, p) => sum + (Number(p.unrealized_pnl) || 0), 0)
      : null;

  // 账户余额
  const walletBalance = account?.wallet_balance;
  const availableBalance = account?.available_balance;

  // 策略运行状态
  const isStrategyActive = tradingStatus?.is_active === true;
  const strategyStatusText = tradingStatus?.entry_paused
    ? "暂无通过验证的生产策略"
    : tradingStatus?.is_active === true
      ? "运行中"
    : tradingStatus?.is_active === false
      ? "已暂停"
      : "状态未知";

  // 模拟盘连接状态
  const isExchangeConnected = accountAvailable;
  const exchangeStatusText = account?.status === "stale"
    ? "数据延迟"
    : isExchangeConnected
      ? "已连接"
      : account?.status === "loading"
        ? "连接中"
        : "连接失败";

  // 行情状态
  const feedStatusText =
    streamStatus === "live"
      ? "实时"
      : streamStatus === "polling"
        ? "轮询更新"
        : streamStatus === "offline"
          ? "服务不可用"
          : "连接中";

  // 风控状态
  const riskStatusText = globalRiskStatus?.entry_allowed === false
    ? (globalRiskStatus?.status === "blocked" ? "已阻断" : "限制开仓")
    : globalRiskStatus?.entry_allowed === true
      ? "正常"
      : "状态待确认";

  // 最新决策摘要
  const latestDecision = Array.isArray(decisions) && decisions.length > 0 ? decisions[0] : null;
  const decisionSymbol = latestDecision?.symbol || selectedSymbol;
  const decisionReasonText = latestDecision ? formatDecisionReason(latestDecision.terminal_reason || latestDecision.reason_code) : "暂无决策";

  // 后端字段是 web_ui_url；断连时也必须保留默认可跳转入口（勿依赖错误字段 testnet_url）
  const binanceTestnetUrl = account?.web_ui_url || DEFAULT_BINANCE_TESTNET_URL;
  // Backend errors are technical English. Present them in Chinese, and keep the raw
  // text as a tooltip so the exact backend reason is still recoverable.
  const rawConnectionError = !isExchangeConnected && account?.error ? String(account.error) : null;
  const connectionError = rawConnectionError ? formatRuntimeError(rawConnectionError) : null;
  const noTradeReason = noTradeSummary?.entry_runtime?.reason ?? noTradeSummary?.decisions?.dominant_reason;
  const noTradeCount = noTradeReason
    ? Number(noTradeSummary?.decisions?.reason_counts?.[noTradeReason] ?? 0)
    : 0;
  const noTradeConclusion = formatNoTradeSummary(noTradeSummary?.summary_code, noTradeReason, noTradeCount);
  const noTradeHours = noTradeSummary?.hours_since_last_entry;

  return (
    <div className="trading-summary-hero">
      <div className="trading-summary-heading">
        <h1>AI 量化自动交易总览</h1>
        <p className="trading-summary-subtitle">
          策略扫描、币安模拟盘账户、持仓与最新决策
        </p>
      </div>

      <div className="trading-summary-metrics">
        <div className="metric-item">
          <span className="metric-label">模拟账户余额</span>
          <span className="metric-value">
            {walletBalance != null ? `${Number(walletBalance).toFixed(2)} USDT` : "暂不可用"}
          </span>
        </div>
        <div className="metric-item">
          <span className="metric-label">可用资金</span>
          <span className="metric-value">
            {availableBalance != null ? `${Number(availableBalance).toFixed(2)} USDT` : "暂不可用"}
          </span>
        </div>
        <div className="metric-item">
          <span className="metric-label">当前持仓</span>
          <span className="metric-value">{positionCount ?? "暂不可用"}</span>
        </div>
        <div className="metric-item">
          <span className="metric-label">浮动盈亏</span>
          <span className={`metric-value ${unrealizedPnl != null ? (unrealizedPnl >= 0 ? "pnl-positive" : "pnl-negative") : ""}`}>
            {unrealizedPnl != null ? `${unrealizedPnl >= 0 ? "+" : ""}${unrealizedPnl.toFixed(2)} USDT` : "暂不可用"}
          </span>
        </div>
        <div className="metric-item">
          <span className="metric-label">未成交订单</span>
          <span className="metric-value">{orderCount ?? "暂不可用"}</span>
        </div>
      </div>

      <div className="trading-summary-status">
        <div className="status-item">
          <span className="status-label">自动策略</span>
          <span className={`status-value ${isStrategyActive ? "status-active" : "status-inactive"}`}>
            {strategyStatusText}
          </span>
        </div>
        <div className="status-item">
          <span className="status-label">模拟盘</span>
          <span className={`status-value ${isExchangeConnected ? "status-connected" : "status-disconnected"}`}>
            {exchangeStatusText}
          </span>
        </div>
        <div className="status-item">
          <span className="status-label">行情</span>
          <span className="status-value">{feedStatusText}</span>
        </div>
        <div className="status-item">
          <span className="status-label">风险控制</span>
          <span className={`status-value ${globalRiskStatus?.entry_allowed !== true ? "status-warning" : ""}`}>
            {riskStatusText}
          </span>
        </div>
      </div>

      {latestDecision && (
        <div className="trading-summary-decision">
          <h3>最新决策 ({decisionSymbol})</h3>
          <p className="decision-reason">{decisionReasonText}</p>
          {latestDecision.created_at && (
            <p className="decision-time">
              {new Date(latestDecision.created_at).toLocaleString("zh-CN")}
            </p>
          )}
        </div>
      )}

      {noTradeSummary ? (
        <section className="trading-summary-no-trade" aria-label="不开单监控">
          <h3>不开单监控</h3>
          <p>{noTradeHours != null ? `已 ${formatNumber(noTradeHours, 1)} 小时未产生新开仓` : "当前查询窗口内没有新开仓成交记录"}</p>
          <strong>{noTradeConclusion}</strong>
        </section>
      ) : null}

      <div className="trading-summary-actions">
        <a
          href={binanceTestnetUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="trading-summary-binance-link"
        >
          打开币安模拟盘
        </a>
        <span className="trading-summary-action-hint" title={rawConnectionError ?? undefined}>
          {isExchangeConnected
            ? "用同一模拟账户对账；勿用主网 futures.binance.com"
            : connectionError
              ? `账户未接通：${connectionError}`
              : "账户未接通时仍可先打开模拟盘网页；本平台需凭证+代理后才会同步余额"}
        </span>
      </div>
    </div>
  );
}
