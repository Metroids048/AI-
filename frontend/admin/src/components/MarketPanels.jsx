import { useEffect, useMemo, useRef } from "react";
import { CandlestickSeries, createChart } from "lightweight-charts";

import { formatNumber, formatPercent, formatTime } from "../utils/format";
import { Metric } from "./Common";

export const TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h", "1d"];

export function MarketHeader({ snapshot, symbol, perpSymbol, timeframe, onTimeframeChange, feedStatus }) {
  const freshness = snapshot?.data_freshness ?? {};
  const delay =
    freshness.delay_seconds === null || freshness.delay_seconds === undefined
      ? "--"
      : `${Math.round(Number(freshness.delay_seconds) / 60)} 分钟`;
  const feedLabel =
    feedStatus?.status === "live"
      ? "Binance WS"
      : feedStatus?.status === "rest_polling"
        ? "REST 轮询"
        : "连接中";

  return (
    <section className="market-header exchange-panel">
      <div className="symbol-block">
        <strong>{perpSymbol}</strong>
        <span>{symbol} / Binance USDT 永续 Paper</span>
      </div>
      <div className="timeframe-tabs" role="group" aria-label="K线周期">
        {TIMEFRAMES.map((item) => (
          <button
            key={item}
            type="button"
            className={item === timeframe ? "active" : ""}
            onClick={() => onTimeframeChange(item)}
          >
            {item}
          </button>
        ))}
      </div>
      <div className="quote-grid">
        <Metric label="现货价格" value={formatNumber(snapshot?.spot_last_price)} />
        <Metric label="永续价格" value={formatNumber(snapshot?.perp_last_price)} />
        <Metric label="基差" value={`${formatNumber(snapshot?.basis_bps, 2)} bps`} />
        <Metric label="资金费率" value={formatPercent(snapshot?.funding_rate)} />
        <Metric label="下次 Funding" value={formatTime(snapshot?.next_funding_at)} />
        <Metric label="行情源" value={`${feedLabel} / ${delay}`} />
      </div>
    </section>
  );
}

export function KlinePanel({ candles, latestKline, snapshotVersion, orders, symbol, timeframe, streamStatus }) {
  const containerRef = useRef(null);
  const chartRef = useRef(null);
  const seriesRef = useRef(null);
  const priceLinesRef = useRef([]);
  const initializedRef = useRef(false);
  const chartData = useMemo(() => normalizeCandles(candles), [candles]);
  const latestPoint = useMemo(() => normalizeCandle(latestKline), [latestKline]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return undefined;
    const chart = createChart(container, {
      autoSize: true,
      layout: { background: { color: "#ffffff" }, textColor: "#707a8a" },
      grid: {
        vertLines: { color: "rgba(112, 122, 138, 0.10)" },
        horzLines: { color: "rgba(112, 122, 138, 0.10)" },
      },
      rightPriceScale: { borderColor: "#eaecef" },
      timeScale: { borderColor: "#eaecef" },
      crosshair: { mode: 0 },
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
      initializedRef.current = false;
    };
  }, []);

  useEffect(() => {
    if (!seriesRef.current || !chartRef.current) return;
    seriesRef.current.setData(chartData);
    initializedRef.current = true;
    if (chartData.length) chartRef.current.timeScale().fitContent();
  }, [snapshotVersion]);

  useEffect(() => {
    if (!seriesRef.current || !latestPoint || !initializedRef.current) return;
    seriesRef.current.update(latestPoint);
  }, [latestPoint]);

  useEffect(() => {
    if (!seriesRef.current) return;
    for (const line of priceLinesRef.current) {
      seriesRef.current.removePriceLine(line);
    }
    priceLinesRef.current = buildRiskPriceLines(orders, { chartSymbol: symbol }).map((line) =>
      seriesRef.current.createPriceLine(line),
    );
  }, [orders, symbol]);

  const rejectedOrders = (orders ?? []).filter((order) => order.execution_status === "rejected");
  const streamLabel = streamStatus === "live" ? "实时" : streamStatus === "connecting" ? "连接中" : "REST 轮询";
  return (
    <section className="exchange-panel kline-panel">
      <div className="panel-title">
        <h2>{symbol} K线</h2>
        <span>{timeframe} / {streamLabel}</span>
      </div>
      <div className="chart-box" ref={containerRef}>
        {!chartData.length ? <div className="empty-overlay">暂无 K 线数据</div> : null}
      </div>
      <div className="marker-strip">
        <span>当前蜡烛：实时 update</span>
        <span>拒单：{rejectedOrders.length}</span>
        <span>SL/TP：审计价格线</span>
      </div>
    </section>
  );
}

export function buildRiskPriceLines(orders, { chartSymbol } = {}) {
  const lines = [];
  for (const order of orders ?? []) {
    const status = String(order?.execution_status || "").toLowerCase();
    if (status === "rejected" || status === "cancelled" || status === "canceled") {
      continue;
    }
    if (chartSymbol) {
      const orderSymbol = String(order?.symbol || "");
      const chartBase = String(chartSymbol).replace(":USDT", "");
      if (orderSymbol && orderSymbol !== chartSymbol && orderSymbol !== chartBase) {
        continue;
      }
    }
    const stoploss = Number(order?.stoploss_plan?.price);
    const takeprofit = Number(order?.takeprofit_plan?.price);
    if (Number.isFinite(stoploss) && stoploss > 0) {
      lines.push({
        price: stoploss,
        color: "#f6465d",
        lineWidth: 1,
        lineStyle: 2,
        axisLabelVisible: true,
        title: `SL ${order.symbol ?? ""}`.trim(),
      });
    }
    if (Number.isFinite(takeprofit) && takeprofit > 0) {
      lines.push({
        price: takeprofit,
        color: "#16c784",
        lineWidth: 1,
        lineStyle: 2,
        axisLabelVisible: true,
        title: `TP ${order.symbol ?? ""}`.trim(),
      });
    }
  }
  return lines;
}

function normalizeCandles(candles) {
  return (candles ?? []).map(normalizeCandle).filter(Boolean);
}

function normalizeCandle(item) {
  if (!item) return null;
  const timestamp = item.time ?? item.timestamp;
  const point = {
    time: Math.floor(new Date(timestamp).getTime() / 1000),
    open: Number(item.open),
    high: Number(item.high),
    low: Number(item.low),
    close: Number(item.close),
  };
  if (!Number.isFinite(point.time) || !Number.isFinite(point.close)) return null;
  return point;
}
