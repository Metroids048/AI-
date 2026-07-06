import { useCallback, useEffect, useState } from "react";

import { request, streamUrl } from "../api/client";

export function useConsoleData(symbol, perpSymbol, timeframe) {
  const [state, setState] = useState({
    overview: null,
    snapshot: null,
    candles: [],
    universe: [],
    tradingStatus: null,
    fundingSignal: null,
    orderBook: null,
    trades: null,
    decisionTrace: null,
    newsItems: [],
    macroEvents: [],
    reviews: [],
    notifications: [],
    streamStatus: "connecting",
    error: "",
    loading: true,
  });

  const refresh = useCallback(async () => {
    setState((current) => ({ ...current, error: "" }));
    try {
      const params = new URLSearchParams({ symbol, perp_symbol: perpSymbol, timeframe });
      const overviewPayload = await request(`/api/v1/console/overview?${params.toString()}`);
      const latestPaper = Array.isArray(overviewPayload.paper_runs) ? overviewPayload.paper_runs.at(-1) : null;
      const [
        snapshotPayload,
        candlesPayload,
        universePayload,
        tradingStatusPayload,
        fundingSignalPayload,
        orderBookPayload,
        tradesPayload,
        decisionPayload,
        newsPayload,
        macroPayload,
        reviewsPayload,
        notificationsPayload,
      ] = await Promise.all([
        request(`/api/v1/market/snapshot?${params.toString()}`),
        request(`/api/v1/market/ohlcv?${new URLSearchParams({ symbol, timeframe, limit: "300" }).toString()}`),
        request("/api/v1/market/universe?limit=20"),
        request("/api/v1/execution/trading-status"),
        request(`/api/v1/market/funding-arbitrage-signal?${params.toString()}`),
        request(`/api/v1/market/order-book?${new URLSearchParams({ symbol: perpSymbol, limit: "20" }).toString()}`),
        request(`/api/v1/market/trades?${new URLSearchParams({ symbol: perpSymbol, limit: "50" }).toString()}`),
        latestPaper?.paper_run_id
          ? request(`/api/v1/execution/paper-runs/${latestPaper.paper_run_id}/decision-trace`)
          : Promise.resolve(null),
        request("/api/v1/market/news?limit=20"),
        request("/api/v1/market/macro-events?limit=20"),
        request("/api/v1/reviews"),
        request("/api/v1/notifications/outbox?limit=20"),
      ]);
      setState((current) => ({
        ...current,
        overview: overviewPayload,
        snapshot: snapshotPayload,
        candles: candlesPayload.candles ?? [],
        universe: universePayload.items ?? [],
        tradingStatus: tradingStatusPayload,
        fundingSignal: fundingSignalPayload,
        orderBook: orderBookPayload,
        trades: tradesPayload,
        decisionTrace: decisionPayload,
        newsItems: newsPayload.items ?? [],
        macroEvents: macroPayload.items ?? [],
        reviews: reviewsPayload.items ?? [],
        notifications: notificationsPayload.items ?? [],
        error: "",
        loading: false,
      }));
    } catch (err) {
      setState((current) => ({ ...current, error: err.message, loading: false }));
    }
  }, [symbol, perpSymbol, timeframe]);

  useEffect(() => {
    refresh();
    const timer = window.setInterval(refresh, 5000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  useEffect(() => {
    if (!("WebSocket" in window)) {
      setState((current) => ({ ...current, streamStatus: "polling" }));
      return undefined;
    }

    const params = new URLSearchParams({ symbol, timeframe, limit: "300" });
    const socket = new WebSocket(streamUrl(`/api/v1/market/ohlcv/stream?${params.toString()}`));
    setState((current) => ({ ...current, streamStatus: "connecting" }));

    socket.onopen = () => setState((current) => ({ ...current, streamStatus: "live" }));
    socket.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data);
        const candles = message?.payload?.candles;
        if (Array.isArray(candles)) {
          setState((current) => ({ ...current, candles, streamStatus: "live" }));
        }
      } catch {
        setState((current) => ({ ...current, streamStatus: "polling" }));
      }
    };
    socket.onerror = () => setState((current) => ({ ...current, streamStatus: "polling" }));
    socket.onclose = () => setState((current) => ({ ...current, streamStatus: "polling" }));

    return () => socket.close();
  }, [symbol, timeframe]);

  return { ...state, refresh };
}
