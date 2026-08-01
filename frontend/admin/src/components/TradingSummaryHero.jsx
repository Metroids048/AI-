/**
 * 交易总览 Hero - 主页核心信息卡片
 * 展示账户、持仓、盈亏、策略状态等关键指标
 */
export function TradingSummaryHero({
  account,
  positions,
  orders,
  decisions,
  tradingStatus,
  globalRiskStatus,
  streamStatus,
  selectedSymbol,
  lastSuccessAt,
}) {
  const positionCount = Array.isArray(positions) ? positions.length : 0;
  const orderCount = Array.isArray(orders) ? orders.length : 0;

  // 计算浮动盈亏
  const unrealizedPnl = Array.isArray(positions)
    ? positions.reduce((sum, p) => sum + (Number(p.unrealized_pnl) || 0), 0)
    : null;

  // 账户余额
  const walletBalance = account?.wallet_balance;
  const availableBalance = account?.available_balance;

  // 策略运行状态
  const isStrategyActive = tradingStatus?.is_active === true;
  const strategyStatusText = tradingStatus?.is_active === true
    ? "运行中"
    : tradingStatus?.is_active === false
      ? "已暂停"
      : "状态未知";

  // 模拟盘连接状态
  const isExchangeConnected = account?.connected === true;
  const exchangeStatusText = isExchangeConnected ? "已连接" : "未连接";

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
  const riskStatusText =
    globalRiskStatus?.entry_allowed === false ? "已暂停新开仓" : "正常";

  // 最新决策摘要
  const latestDecision = Array.isArray(decisions) && decisions.length > 0 ? decisions[0] : null;
  const decisionSymbol = latestDecision?.symbol || selectedSymbol;
  const decisionReasonText = latestDecision?.terminal_reason || "暂无决策";

  // 币安模拟盘 URL
  const binanceTestnetUrl = account?.testnet_url || null;

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
            {walletBalance != null ? `${Number(walletBalance).toFixed(2)} USDT` : "未接通"}
          </span>
        </div>
        <div className="metric-item">
          <span className="metric-label">可用资金</span>
          <span className="metric-value">
            {availableBalance != null ? `${Number(availableBalance).toFixed(2)} USDT` : "未接通"}
          </span>
        </div>
        <div className="metric-item">
          <span className="metric-label">当前持仓</span>
          <span className="metric-value">{positionCount}</span>
        </div>
        <div className="metric-item">
          <span className="metric-label">浮动盈亏</span>
          <span className={`metric-value ${unrealizedPnl != null ? (unrealizedPnl >= 0 ? "pnl-positive" : "pnl-negative") : ""}`}>
            {unrealizedPnl != null ? `${unrealizedPnl >= 0 ? "+" : ""}${unrealizedPnl.toFixed(2)} USDT` : "暂无数据"}
          </span>
        </div>
        <div className="metric-item">
          <span className="metric-label">未成交订单</span>
          <span className="metric-value">{orderCount}</span>
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
          <span className={`status-value ${globalRiskStatus?.entry_allowed === false ? "status-warning" : ""}`}>
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

      {binanceTestnetUrl && (
        <div className="trading-summary-actions">
          <a
            href={binanceTestnetUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="btn-secondary btn-sm"
          >
            打开币安模拟盘
          </a>
        </div>
      )}
    </div>
  );
}
