import { useCallback, useEffect, useState } from "react";

import { request } from "../api/client";

export function useConsoleData(symbol, perpSymbol, timeframe) {
  const [state, setState] = useState({
    overview: null,
    snapshot: null,
    candles: [],
    decisionTrace: null,
    newsItems: [],
    macroEvents: [],
    reviews: [],
    notifications: [],
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
        decisionPayload,
        newsPayload,
        macroPayload,
        reviewsPayload,
        notificationsPayload,
      ] = await Promise.all([
        request(`/api/v1/market/snapshot?${params.toString()}`),
        request(`/api/v1/market/ohlcv?${new URLSearchParams({ symbol, timeframe, limit: "300" }).toString()}`),
        latestPaper?.paper_run_id
          ? request(`/api/v1/execution/paper-runs/${latestPaper.paper_run_id}/decision-trace`)
          : Promise.resolve(null),
        request("/api/v1/market/news?limit=20"),
        request("/api/v1/market/macro-events?limit=20"),
        request("/api/v1/reviews"),
        request("/api/v1/notifications/outbox?limit=20"),
      ]);
      setState({
        overview: overviewPayload,
        snapshot: snapshotPayload,
        candles: candlesPayload.candles ?? [],
        decisionTrace: decisionPayload,
        newsItems: newsPayload.items ?? [],
        macroEvents: macroPayload.items ?? [],
        reviews: reviewsPayload.items ?? [],
        notifications: notificationsPayload.items ?? [],
        error: "",
        loading: false,
      });
    } catch (err) {
      setState((current) => ({ ...current, error: err.message, loading: false }));
    }
  }, [symbol, perpSymbol, timeframe]);

  useEffect(() => {
    refresh();
    const timer = window.setInterval(refresh, 5000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  return { ...state, refresh };
}
