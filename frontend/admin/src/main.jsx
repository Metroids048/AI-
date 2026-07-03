import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactDOM from "react-dom/client";
import { CandlestickSeries, createChart } from "lightweight-charts";

import "./styles.css";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "";
const DEFAULT_SYMBOL = "BTC/USDT";
const DEFAULT_PERP = "BTC/USDT:USDT";
const TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h", "1d"];

function apiUrl(path) {
  return `${API_BASE}${path}`;
}

async function request(path, options = {}) {
  const response = await fetch(apiUrl(path), {
    headers: { "Content-Type": "application/json", ...(options.headers ?? {}) },
    ...options,
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const message = payload?.message ?? payload?.detail ?? response.statusText;
    throw new Error(typeof message === "string" ? message : "请求失败");
  }
  return payload;
}

function formatNumber(value, digits = 2) {
  if (value === null || value === undefined || value === "") return "数据缺失";
  const number = Number(value);
  if (!Number.isFinite(number)) return "数据缺失";
  return new Intl.NumberFormat("zh-CN", {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  }).format(number);
}

function formatPercent(value) {
  if (value === null || value === undefined || value === "") return "数据缺失";
  const number = Number(value);
  if (!Number.isFinite(number)) return "数据缺失";
  return `${(number * 100).toFixed(4)}%`;
}

function formatTime(value) {
  if (!value) return "数据缺失";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "数据缺失";
  return date.toLocaleString("zh-CN", { hour12: false });
}

function asArray(value) {
  return Array.isArray(value) ? value : [];
}

function StatusPill({ label, value, tone = "neutral" }) {
  return (
    <div className={`status-pill status-${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function AppShell({ overview, snapshot, error, children }) {
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

function MarketHeader({ snapshot, symbol, setSymbol, perpSymbol, setPerpSymbol, timeframe, setTimeframe }) {
  const freshness = snapshot?.data_freshness ?? {};
  const delay = freshness.delay_seconds === null || freshness.delay_seconds === undefined
    ? "数据缺失"
    : `${Math.round(freshness.delay_seconds / 60)} 分钟`;

  return (
    <section className="market-header panel">
      <div className="field-grid">
        <label>
          Spot
          <input value={symbol} onChange={(event) => setSymbol(event.target.value)} />
        </label>
        <label>
          Perp
          <input value={perpSymbol} onChange={(event) => setPerpSymbol(event.target.value)} />
        </label>
        <label>
          Timeframe
          <select value={timeframe} onChange={(event) => setTimeframe(event.target.value)}>
            {TIMEFRAMES.map((item) => (
              <option key={item}>{item}</option>
            ))}
          </select>
        </label>
      </div>
      <div className="quote-grid">
        <Metric label="Spot 最新价" value={formatNumber(snapshot?.spot_last_price)} />
        <Metric label="Perp 最新价" value={formatNumber(snapshot?.perp_last_price)} />
        <Metric label="基差" value={`${formatNumber(snapshot?.basis_bps, 2)} bps`} />
        <Metric label="资金费率" value={formatPercent(snapshot?.funding_rate)} />
        <Metric label="下一次 Funding" value={formatTime(snapshot?.next_funding_at)} />
        <Metric label="数据延迟" value={delay} />
      </div>
    </section>
  );
}

function Metric({ label, value, tone = "neutral" }) {
  return (
    <div className={`metric metric-${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function KlinePanel({ candles, orders, timeframe }) {
  const containerRef = useRef(null);
  const chartRef = useRef(null);
  const seriesRef = useRef(null);

  const chartData = useMemo(
    () =>
      asArray(candles).map((item) => ({
        time: Math.floor(new Date(item.time ?? item.timestamp).getTime() / 1000),
        open: Number(item.open),
        high: Number(item.high),
        low: Number(item.low),
        close: Number(item.close),
      })).filter((item) => Number.isFinite(item.time) && Number.isFinite(item.close)),
    [candles],
  );

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return undefined;
    const chart = createChart(container, {
      autoSize: true,
      layout: {
        background: { color: "#0f1720" },
        textColor: "#a9b7c7",
      },
      grid: {
        vertLines: { color: "rgba(148, 163, 184, 0.10)" },
        horzLines: { color: "rgba(148, 163, 184, 0.10)" },
      },
      rightPriceScale: { borderColor: "rgba(148, 163, 184, 0.25)" },
      timeScale: { borderColor: "rgba(148, 163, 184, 0.25)" },
    });
    const series = chart.addSeries(CandlestickSeries, {
      upColor: "#22c55e",
      downColor: "#ef4444",
      borderVisible: false,
      wickUpColor: "#22c55e",
      wickDownColor: "#ef4444",
    });
    chartRef.current = chart;
    seriesRef.current = series;
    const resizeObserver = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (!entry) return;
      chart.resize(Math.floor(entry.contentRect.width), Math.floor(entry.contentRect.height));
    });
    resizeObserver.observe(container);
    return () => {
      resizeObserver.disconnect();
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (!seriesRef.current || !chartRef.current) return;
    seriesRef.current.setData(chartData);
    if (chartData.length) chartRef.current.timeScale().fitContent();
  }, [chartData]);

  const rejectedOrders = asArray(orders).filter((order) => order.execution_status === "rejected");

  return (
    <section className="panel kline-panel">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">Binance OHLCV</p>
          <h2>K线与执行标记</h2>
        </div>
        <span>{timeframe}</span>
      </div>
      <div className="chart-box" ref={containerRef}>
        {!chartData.length ? <div className="empty-overlay">K线数据缺失</div> : null}
      </div>
      <div className="marker-strip">
        <span>Funding 标记：读取 market_extras</span>
        <span>拒绝信号：{rejectedOrders.length}</span>
        <span>成交标记：后续由交易所回报接入</span>
      </div>
    </section>
  );
}

function CarryPanel({ snapshot, latestBacktests }) {
  const latest = asArray(latestBacktests).at(-1);
  const metrics = latest?.metrics_summary ?? {};
  const gate = latest?.eligibility_result ?? {};
  const feeBps = metrics.cost_breakdown_bps?.fee_bps;
  const slippageBps = metrics.cost_breakdown_bps?.slippage_bps;
  const fundingBps = metrics.cost_breakdown_bps?.funding_bps;
  const estimatedNet = [snapshot?.basis_bps, fundingBps, feeBps, slippageBps].every((item) => item !== undefined && item !== null)
    ? Number(snapshot.basis_bps) + Number(fundingBps) - Number(feeBps) - Number(slippageBps)
    : null;

  return (
    <section className="panel carry-panel">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">Funding Carry</p>
          <h2>资金费率套利</h2>
        </div>
        <span className={`gate-badge ${gate.decision_status === "rejected_with_reason" ? "bad" : ""}`}>
          {gate.decision_status ?? "尚无 Gate"}
        </span>
      </div>
      <div className="metric-grid compact">
        <Metric label="当前 Funding" value={formatPercent(snapshot?.funding_rate)} />
        <Metric label="当前基差" value={`${formatNumber(snapshot?.basis_bps, 2)} bps`} />
        <Metric label="手续费" value={`${formatNumber(feeBps, 2)} bps`} />
        <Metric label="滑点" value={`${formatNumber(slippageBps, 2)} bps`} />
        <Metric label="Funding 收益/成本" value={`${formatNumber(fundingBps, 2)} bps`} />
        <Metric label="估算净边际" value={`${formatNumber(estimatedNet, 2)} bps`} tone={estimatedNet > 0 ? "positive" : "neutral"} />
      </div>
      <div className="gate-box">
        <span>最近回测</span>
        <strong>{latest?.backtest_run_id ?? "数据缺失"}</strong>
        <p>{gate.reason ?? "尚未产生准入/拒绝原因"}</p>
      </div>
    </section>
  );
}

function PaperRunControls({ onAction, overview }) {
  const [strategyId, setStrategyId] = useState("");
  const [gateRef, setGateRef] = useState("");
  const [riskText, setRiskText] = useState("手动高严重度风险事件");
  const latestPaper = asArray(overview?.paper_runs).at(-1);

  return (
    <section className="panel controls-panel">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">Manual Gate</p>
          <h2>人工操作</h2>
        </div>
        <span>Paper only</span>
      </div>
      <div className="control-grid">
        <label>
          Strategy ID
          <input value={strategyId} onChange={(event) => setStrategyId(event.target.value)} placeholder="strategy-id" />
        </label>
        <label>
          Gate / Backtest ID
          <input value={gateRef} onChange={(event) => setGateRef(event.target.value)} placeholder="backtest-run-id" />
        </label>
        <button
          type="button"
          onClick={() => onAction("startPaper", { strategy_id: strategyId, gate_decision_ref: gateRef })}
          disabled={!strategyId || !gateRef}
        >
          启动 Paper
        </button>
        <button
          type="button"
          onClick={() => onAction("pausePaper", { paper_run_id: latestPaper?.paper_run_id })}
          disabled={!latestPaper?.paper_run_id}
        >
          暂停 Paper
        </button>
        <button
          type="button"
          onClick={() => onAction("carryBacktest", { strategy_id: strategyId })}
          disabled={!strategyId}
        >
          触发 Carry 回测
        </button>
      </div>
      <div className="risk-submit">
        <input value={riskText} onChange={(event) => setRiskText(event.target.value)} />
        <button type="button" onClick={() => onAction("createRisk", { description: riskText })}>
          提交风险事件
        </button>
      </div>
    </section>
  );
}

function OrdersTable({ orders }) {
  const rows = asArray(orders);
  return (
    <section className="panel table-panel">
      <div className="panel-heading">
        <h2>订单与 Gatekeeper</h2>
        <span>{rows.length}</span>
      </div>
      <table>
        <thead>
          <tr>
            <th>时间</th>
            <th>Symbol</th>
            <th>方向</th>
            <th>状态</th>
            <th>拒绝原因</th>
          </tr>
        </thead>
        <tbody>
          {rows.length ? rows.map((order) => (
            <tr key={order.order_execution_id ?? `${order.symbol}-${order.created_at}`}>
              <td>{formatTime(order.created_at)}</td>
              <td>{order.symbol}</td>
              <td>{order.direction}</td>
              <td>{order.execution_status}</td>
              <td>{order.rejection_reason ?? "无"}</td>
            </tr>
          )) : (
            <tr><td colSpan="5">订单数据缺失</td></tr>
          )}
        </tbody>
      </table>
    </section>
  );
}

function PositionsTable({ positions }) {
  const rows = asArray(positions);
  return (
    <section className="panel table-panel">
      <div className="panel-heading">
        <h2>持仓</h2>
        <span>{rows.length}</span>
      </div>
      <table>
        <thead>
          <tr>
            <th>时间</th>
            <th>Symbol</th>
            <th>方向</th>
            <th>数量</th>
            <th>入场</th>
            <th>标记</th>
            <th>未实现 PnL</th>
          </tr>
        </thead>
        <tbody>
          {rows.length ? rows.map((position) => (
            <tr key={position.position_snapshot_id ?? `${position.symbol}-${position.snapshot_time}`}>
              <td>{formatTime(position.snapshot_time)}</td>
              <td>{position.symbol}</td>
              <td>{position.side}</td>
              <td>{formatNumber(position.quantity, 4)}</td>
              <td>{formatNumber(position.entry_price)}</td>
              <td>{formatNumber(position.mark_price)}</td>
              <td>{formatNumber(position.unrealized_pnl)}</td>
            </tr>
          )) : (
            <tr><td colSpan="7">持仓数据缺失</td></tr>
          )}
        </tbody>
      </table>
    </section>
  );
}

function RiskEventFeed({ events, onAcknowledge }) {
  const rows = asArray(events);
  return (
    <section className="panel risk-panel">
      <div className="panel-heading">
        <h2>风险事件</h2>
        <span>{rows.length}</span>
      </div>
      <div className="risk-list">
        {rows.length ? rows.map((event) => (
          <article key={event.risk_event_id ?? event.description} className={`risk-item severity-${event.severity}`}>
            <div>
              <strong>{event.severity}</strong>
              <span>{event.event_type}</span>
            </div>
            <p>{event.description}</p>
            <footer>
              <span>{event.resolution_status}</span>
              <button
                type="button"
                onClick={() => onAcknowledge(event.risk_event_id)}
                disabled={!event.risk_event_id || event.resolution_status === "acknowledged"}
              >
                确认
              </button>
            </footer>
          </article>
        )) : <div className="empty-list">暂无活跃风险事件</div>}
      </div>
    </section>
  );
}

function useConsoleData(symbol, perpSymbol, timeframe) {
  const [overview, setOverview] = useState(null);
  const [snapshot, setSnapshot] = useState(null);
  const [candles, setCandles] = useState([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    setError("");
    try {
      const params = new URLSearchParams({ symbol, perp_symbol: perpSymbol, timeframe });
      const [overviewPayload, snapshotPayload, candlesPayload] = await Promise.all([
        request(`/api/v1/console/overview?${params.toString()}`),
        request(`/api/v1/market/snapshot?${params.toString()}`),
        request(`/api/v1/market/ohlcv?${new URLSearchParams({ symbol, timeframe, limit: "300" }).toString()}`),
      ]);
      setOverview(overviewPayload);
      setSnapshot(snapshotPayload);
      setCandles(candlesPayload.candles ?? []);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [symbol, perpSymbol, timeframe]);

  useEffect(() => {
    refresh();
    const timer = window.setInterval(refresh, 5000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  return { overview, snapshot, candles, error, loading, refresh };
}

function App() {
  const [symbol, setSymbol] = useState(DEFAULT_SYMBOL);
  const [perpSymbol, setPerpSymbol] = useState(DEFAULT_PERP);
  const [timeframe, setTimeframe] = useState("1h");
  const [actionMessage, setActionMessage] = useState("");
  const { overview, snapshot, candles, error, loading, refresh } = useConsoleData(symbol, perpSymbol, timeframe);

  const handleAction = async (type, payload) => {
    setActionMessage("");
    try {
      if (type === "startPaper") {
        await request("/api/v1/execution/paper-runs", {
          method: "POST",
          body: JSON.stringify(payload),
        });
        setActionMessage("Paper run 已提交");
      }
      if (type === "pausePaper") {
        await request(`/api/v1/execution/paper-runs/${payload.paper_run_id}/status`, {
          method: "PATCH",
          body: JSON.stringify({ paper_status: "paused" }),
        });
        setActionMessage("Paper run 已暂停");
      }
      if (type === "carryBacktest") {
        const now = new Date();
        const start = new Date(now.getTime() - 16 * 60 * 60 * 1000);
        await request("/api/v1/backtests/carry", {
          method: "POST",
          body: JSON.stringify({
            strategy_id: payload.strategy_id,
            spot_symbol: symbol,
            perp_symbol: perpSymbol,
            timeframe,
            start_at: start.toISOString(),
            end_at: now.toISOString(),
          }),
        });
        setActionMessage("Carry 回测已提交");
      }
      if (type === "createRisk") {
        await request("/api/v1/risk/events", {
          method: "POST",
          body: JSON.stringify({
            event_type: "exchange_incident",
            severity: "high",
            source: "manual_console",
            description: payload.description,
            affected_scope: [symbol],
          }),
        });
        setActionMessage("风险事件已提交");
      }
      await refresh();
    } catch (err) {
      setActionMessage(`操作失败：${err.message}`);
    }
  };

  const handleAcknowledgeRisk = async (riskEventId) => {
    if (!riskEventId) return;
    try {
      await request(`/api/v1/risk/events/${riskEventId}/resolution`, {
        method: "PATCH",
        body: JSON.stringify({ resolution_status: "acknowledged" }),
      });
      setActionMessage("风险事件已确认");
      await refresh();
    } catch (err) {
      setActionMessage(`确认失败：${err.message}`);
    }
  };

  return (
    <AppShell overview={overview} snapshot={snapshot} error={error}>
      {loading ? <div className="loading-line">正在加载控制台数据</div> : null}
      {actionMessage ? <div className="action-line">{actionMessage}</div> : null}
      <MarketHeader
        snapshot={snapshot}
        symbol={symbol}
        setSymbol={setSymbol}
        perpSymbol={perpSymbol}
        setPerpSymbol={setPerpSymbol}
        timeframe={timeframe}
        setTimeframe={setTimeframe}
      />
      <section className="main-grid">
        <KlinePanel candles={candles} orders={overview?.orders} timeframe={timeframe} />
        <CarryPanel snapshot={snapshot} latestBacktests={overview?.latest_backtests} />
        <PaperRunControls overview={overview} onAction={handleAction} />
      </section>
      <section className="bottom-grid">
        <OrdersTable orders={overview?.orders} />
        <PositionsTable positions={overview?.positions} />
        <RiskEventFeed events={overview?.risk_events} onAcknowledge={handleAcknowledgeRisk} />
      </section>
    </AppShell>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
