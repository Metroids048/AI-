export function StatusPill({ label, value, tone = "neutral" }) {
  return (
    <div className={`status-pill status-${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

export function Metric({ label, value, tone = "neutral" }) {
  return (
    <div className={`metric metric-${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

export function HelpTip({ label, children }) {
  return (
    <span className="help-tip" tabIndex="0" aria-label={label} data-tooltip={children}>
      ?
    </span>
  );
}

export function AppShell({ overview, snapshot, tradingStatus, streamStatus, error, children }) {
  const riskTone = overview?.global_risk_status === "blocked" ? "danger" : "ok";
  const dataTone = snapshot?.data_status === "ok" ? "ok" : snapshot?.data_status === "stale" ? "warn" : "neutral";
  const streamTone = streamStatus === "live" ? "ok" : streamStatus === "connecting" ? "warn" : streamStatus === "offline" ? "danger" : "neutral";
  const modeLabel = tradingStatus?.mode === "testnet" ? "币安模拟盘" : "本地模拟盘";
  const streamLabel = streamStatus === "live" ? "实时" : streamStatus === "connecting" ? "连接中" : streamStatus === "offline" ? "服务不可用" : "REST 轮询";
  const dataLabel = dataStatusLabel(snapshot?.data_status);

  return (
    <main className="app-shell">
      <header className={`topbar ${riskTone === "danger" ? "topbar-danger" : ""}`}>
        <div>
          <p className="eyebrow">AI 量化研究平台</p>
          <h1>模拟交易台</h1>
        </div>
        <div className="status-row">
          <StatusPill label="环境" value={overview?.environment === "development" ? "开发环境" : overview?.environment ?? "加载中"} />
          <StatusPill label="模式" value={modeLabel} tone="ok" />
          <StatusPill label="交易所" value={overview?.exchange === "binance" ? "币安" : overview?.exchange ?? "加载中"} />
          <StatusPill label="数据" value={dataLabel} tone={dataTone} />
          <StatusPill label="行情流" value={streamLabel} tone={streamTone} />
          <StatusPill label="风控" value={overview?.global_risk_status === "blocked" ? "已拦截" : "正常"} tone={riskTone} />
        </div>
      </header>
      {error ? <div className="alert-bar">数据加载失败：{error}</div> : null}
      {children}
    </main>
  );
}

function dataStatusLabel(status) {
  if (status === "ok") return "正常";
  if (status === "stale") return "数据延迟";
  if (status === "empty") return "暂无数据";
  if (status === "loading") return "加载中";
  return status ?? "加载中";
}
