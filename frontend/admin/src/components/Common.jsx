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

export function AppShell({ overview, snapshot, error, children }) {
  const riskTone = overview?.global_risk_status === "blocked" ? "danger" : "ok";
  const dataTone = snapshot?.data_status === "ok" ? "ok" : snapshot?.data_status === "stale" ? "warn" : "neutral";

  return (
    <main className="app-shell">
      <header className={`topbar ${riskTone === "danger" ? "topbar-danger" : ""}`}>
        <div>
          <p className="eyebrow">AI Quant Research Platform</p>
          <h1>Paper Trading Console</h1>
        </div>
        <div className="status-row">
          <StatusPill label="环境" value={overview?.environment ?? "dev"} />
          <StatusPill label="模式" value="Paper" tone="ok" />
          <StatusPill label="交易所" value={overview?.exchange ?? "binance"} />
          <StatusPill label="数据" value={snapshot?.data_status ?? "加载中"} tone={dataTone} />
          <StatusPill label="风控" value={overview?.global_risk_status ?? "normal"} tone={riskTone} />
        </div>
      </header>
      {error ? <div className="alert-bar">数据加载失败：{error}</div> : null}
      <nav className="module-tabs" aria-label="console sections">
        {["Research", "Validation", "Paper", "Risk", "Review"].map((item) => (
          <button key={item} className={item === "Paper" ? "active" : ""} type="button">
            {item}
          </button>
        ))}
      </nav>
      {children}
    </main>
  );
}
