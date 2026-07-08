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

export function AppShell({ overview, snapshot, tradingStatus, streamStatus, error, children }) {
  const riskTone = overview?.global_risk_status === "blocked" ? "danger" : "ok";
  const dataTone = snapshot?.data_status === "ok" ? "ok" : snapshot?.data_status === "stale" ? "warn" : "neutral";
  const streamTone = streamStatus === "live" ? "ok" : streamStatus === "connecting" ? "warn" : "neutral";
  const modeLabel = tradingStatus?.mode === "testnet" ? "Testnet" : "Paper";
  const streamLabel = streamStatus === "live" ? "实时" : streamStatus === "connecting" ? "连接中" : "REST 轮询";

  return (
    <main className="app-shell">
      <header className={`topbar ${riskTone === "danger" ? "topbar-danger" : ""}`}>
        <div>
          <p className="eyebrow">AI Quant Research Platform</p>
          <h1>Paper Trading Terminal</h1>
        </div>
        <div className="status-row">
          <StatusPill label="环境" value={overview?.environment ?? "development"} />
          <StatusPill label="模式" value={modeLabel} tone="ok" />
          <StatusPill label="交易所" value={overview?.exchange ?? "binance"} />
          <StatusPill label="数据" value={snapshot?.data_status ?? "加载中"} tone={dataTone} />
          <StatusPill label="行情流" value={streamLabel} tone={streamTone} />
          <StatusPill label="风控" value={overview?.global_risk_status ?? "normal"} tone={riskTone} />
        </div>
      </header>
      {error ? <div className="alert-bar">数据加载失败：{error}</div> : null}
      {children}
    </main>
  );
}
