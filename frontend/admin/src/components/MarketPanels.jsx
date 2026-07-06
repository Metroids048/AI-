import { useEffect, useMemo, useRef } from "react";
import { CandlestickSeries, createChart } from "lightweight-charts";

import { asArray, formatNumber, formatPercent, formatTime } from "../utils/format";
import { Metric } from "./Common";

export const TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h", "1d"];

export function MarketHeader({ snapshot, symbol, setSymbol, perpSymbol, setPerpSymbol, timeframe, setTimeframe }) {
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

export function KlinePanel({ candles, orders, timeframe }) {
  const containerRef = useRef(null);
  const chartRef = useRef(null);
  const seriesRef = useRef(null);
  const chartData = useMemo(
    () =>
      asArray(candles)
        .map((item) => ({
          time: Math.floor(new Date(item.time ?? item.timestamp).getTime() / 1000),
          open: Number(item.open),
          high: Number(item.high),
          low: Number(item.low),
          close: Number(item.close),
        }))
        .filter((item) => Number.isFinite(item.time) && Number.isFinite(item.close)),
    [candles],
  );

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return undefined;
    const chart = createChart(container, {
      autoSize: true,
      layout: { background: { color: "#0f1720" }, textColor: "#a9b7c7" },
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
      if (entry) chart.resize(Math.floor(entry.contentRect.width), Math.floor(entry.contentRect.height));
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
        <span>成交标记：Paper runtime fill</span>
      </div>
    </section>
  );
}

export function CarryPanel({ snapshot, latestBacktests }) {
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
