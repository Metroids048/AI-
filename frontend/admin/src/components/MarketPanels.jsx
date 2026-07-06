import { useEffect, useMemo, useRef } from "react";
import { CandlestickSeries, createChart } from "lightweight-charts";

import { formatNumber, formatPercent, formatTime } from "../utils/format";
import { Metric } from "./Common";

export const TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h", "1d"];

export function MarketHeader({ snapshot, symbol, setSymbol, perpSymbol, setPerpSymbol, timeframe, setTimeframe }) {
  const freshness = snapshot?.data_freshness ?? {};
  const delay =
    freshness.delay_seconds === null || freshness.delay_seconds === undefined
      ? "缺失"
      : `${Math.round(freshness.delay_seconds / 60)} 分钟`;

  return (
    <section className="market-header exchange-panel">
      <div className="symbol-block">
        <strong>{symbol}</strong>
        <span>Binance / USDT 永续研究链路</span>
      </div>
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
          周期
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

export function KlinePanel({ candles, orders, timeframe, streamStatus }) {
  const containerRef = useRef(null);
  const chartRef = useRef(null);
  const seriesRef = useRef(null);
  const chartData = useMemo(
    () =>
      (candles ?? [])
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
      layout: { background: { color: "#0b1118" }, textColor: "#9aa7b6" },
      grid: {
        vertLines: { color: "rgba(148, 163, 184, 0.10)" },
        horzLines: { color: "rgba(148, 163, 184, 0.10)" },
      },
      rightPriceScale: { borderColor: "rgba(148, 163, 184, 0.24)" },
      timeScale: { borderColor: "rgba(148, 163, 184, 0.24)" },
    });
    const series = chart.addSeries(CandlestickSeries, {
      upColor: "#16c784",
      downColor: "#f6465d",
      borderVisible: false,
      wickUpColor: "#16c784",
      wickDownColor: "#f6465d",
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

  const rejectedOrders = (orders ?? []).filter((order) => order.execution_status === "rejected");
  const streamLabel = streamStatus === "live" ? "实时连接" : streamStatus === "connecting" ? "连接中" : "REST 轮询";
  return (
    <section className="exchange-panel kline-panel">
      <div className="panel-title">
        <h2>K线图表</h2>
        <span>{timeframe} / {streamLabel}</span>
      </div>
      <div className="chart-box" ref={containerRef}>
        {!chartData.length ? <div className="empty-overlay">暂无 K 线数据</div> : null}
      </div>
      <div className="marker-strip">
        <span>Funding 标记：market_extras</span>
        <span>拒绝信号：{rejectedOrders.length}</span>
        <span>成交标记：Paper/Testnet 审计</span>
      </div>
    </section>
  );
}
