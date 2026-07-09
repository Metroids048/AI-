import { useCallback, useEffect, useRef, useState } from "react";

import { request, streamUrl } from "../api/client";

const MAX_TRADES = 80;
const FALLBACK_UNIVERSE = [
  { symbol: "BTC/USDT", perp_symbol: "BTC/USDT:USDT", source: "frontend_fallback" },
  { symbol: "ETH/USDT", perp_symbol: "ETH/USDT:USDT", source: "frontend_fallback" },
  { symbol: "SOL/USDT", perp_symbol: "SOL/USDT:USDT", source: "frontend_fallback" },
];

export function useConsoleData(symbol, perpSymbol, timeframe) {
  const [state, setState] = useState({
    overview: null,
    snapshot: null,
    candles: [],
    latestKline: null,
    candleSnapshotVersion: 0,
    universe: FALLBACK_UNIVERSE,
    tradingStatus: null,
    manualContext: null,
    fundingSignal: null,
    intelligenceSignal: null,
    orderBook: null,
    trades: null,
    decisionTrace: null,
    testnetAccount: null,
    feedStatus: null,
    streamStatus: "connecting",
    error: "",
    loading: true,
  });
  const refreshId = useRef(0);
  const selectionRef = useRef("");

  const refresh = useCallback(() => {
    const currentRefresh = refreshId.current + 1;
    refreshId.current = currentRefresh;
    const selectionKey = `${symbol}|${perpSymbol}|${timeframe}`;
    const selectionChanged = selectionRef.current !== selectionKey;
    selectionRef.current = selectionKey;
    setState((current) => ({
      ...current,
      ...(selectionChanged
        ? {
            snapshot: null,
            candles: [],
            latestKline: null,
            orderBook: null,
            trades: null,
            feedStatus: { status: "connecting", source: "websocket" },
            streamStatus: "connecting",
            loading: true,
          }
        : {}),
      error: "",
    }));

    const params = new URLSearchParams({ symbol, perp_symbol: perpSymbol, timeframe });
    const tasks = [];
    const commit = (updater) => {
      if (refreshId.current !== currentRefresh) return;
      setState(updater);
    };
    const run = (path, onPayload, options = {}) => {
      const { reportError = false, showLoaded = true } = options;
      const task = request(path)
        .then((payload) => {
          commit((current) => onPayload(current, payload));
          return payload;
        })
        .catch((err) => {
          if (reportError) {
            commit((current) => ({ ...current, error: err.message, loading: false }));
          }
          return null;
        })
        .finally(() => {
          if (showLoaded) commit((current) => ({ ...current, loading: false }));
        });
      tasks.push(task);
      return task;
    };

    run(
      `/api/v1/console/overview?${params.toString()}`,
      (current, payload) => ({
        ...current,
        overview: payload,
        error: "",
      }),
      { reportError: true },
    ).then((overviewPayload) => {
      const latestPaper = Array.isArray(overviewPayload?.paper_runs) ? overviewPayload.paper_runs.at(-1) : null;
      if (!latestPaper?.paper_run_id || refreshId.current !== currentRefresh) return;
      run(
        `/api/v1/execution/paper-runs/${latestPaper.paper_run_id}/decision-trace`,
        (current, payload) => ({ ...current, decisionTrace: payload }),
        { showLoaded: false },
      );
    });
    run(
      `/api/v1/market/snapshot?${params.toString()}`,
      (current, payload) => ({ ...current, snapshot: payload, error: "" }),
      { reportError: true },
    );
    run(
      `/api/v1/market/ohlcv?${new URLSearchParams({ symbol, timeframe, limit: "300" }).toString()}`,
      (current, payload) => ({
        ...current,
        candles: payload?.candles ?? current.candles,
        // WS kline events already keep candles fresh via incremental update() while live;
        // only force a full chart rebuild when the stream isn't actually live.
        candleSnapshotVersion:
          current.streamStatus === "live" ? current.candleSnapshotVersion : current.candleSnapshotVersion + 1,
        error: "",
      }),
      { reportError: true },
    );
    run(
      `/api/v1/market/order-book?${new URLSearchParams({ symbol: perpSymbol, limit: "20" }).toString()}`,
      (current, payload) => ({ ...current, orderBook: payload ?? current.orderBook, error: "" }),
      { reportError: true },
    );
    run(
      `/api/v1/market/trades?${new URLSearchParams({ symbol: perpSymbol, limit: "50" }).toString()}`,
      (current, payload) => ({ ...current, trades: payload ?? current.trades, error: "" }),
      { reportError: true },
    );
    run(
      "/api/v1/market/universe?limit=20",
      (current, payload) => ({
        ...current,
        universe: payload?.items?.length ? payload.items : current.universe,
      }),
      { showLoaded: false },
    );
    run(
      "/api/v1/execution/trading-status",
      (current, payload) => ({ ...current, tradingStatus: payload ?? current.tradingStatus }),
      { showLoaded: false },
    );
    run(
      "/api/v1/execution/manual-trading-context?mode=paper",
      (current, payload) => ({ ...current, manualContext: payload ?? current.manualContext }),
      { showLoaded: false },
    );
    run(
      "/api/v1/execution/binance-testnet-account",
      (current, payload) => ({ ...current, testnetAccount: payload ?? current.testnetAccount }),
      { showLoaded: false },
    );
    run(
      `/api/v1/market/funding-arbitrage-signal?${params.toString()}`,
      (current, payload) => ({ ...current, fundingSignal: payload ?? current.fundingSignal }),
      { showLoaded: false },
    );
    run(
      `/api/v1/market-intelligence/signals?${new URLSearchParams({ symbol }).toString()}`,
      (current, payload) => ({ ...current, intelligenceSignal: payload ?? current.intelligenceSignal }),
      { showLoaded: false },
    );
    return Promise.allSettled(tasks);
  }, [symbol, perpSymbol, timeframe]);

  useEffect(() => {
    refresh();
    // When the WS stream is live, reduce polling to a 30s fallback to avoid
    // duplicating WS pushes (previously polled every 8s regardless of WS
    // state, wasting bandwidth). When polling-only (WS down), refresh every 8s.
    const interval = state.streamStatus === "live" ? 30000 : 8000;
    const timer = window.setInterval(refresh, interval);
    return () => window.clearInterval(timer);
  }, [refresh, state.streamStatus]);

  useEffect(() => {
    const pollBinance = () => {
      request("/api/v1/execution/binance-testnet-account")
        .then((payload) => setState((current) => ({ ...current, testnetAccount: payload ?? current.testnetAccount })))
        .catch(() => undefined);
    };
    pollBinance();
    const timer = window.setInterval(pollBinance, 5000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    if (!("WebSocket" in window)) {
      setState((current) => ({ ...current, streamStatus: "polling" }));
      return undefined;
    }

    let cancelled = false;
    let socket = null;
    let reconnectTimer = null;
    let reconnectAttempt = 0;

    const connect = () => {
      if (cancelled) return;
      const params = new URLSearchParams({ symbol, perp_symbol: perpSymbol, timeframe, limit: "300" });
      socket = new WebSocket(streamUrl(`/api/v1/market/exchange-stream?${params.toString()}`));

      socket.onopen = () => {
        reconnectAttempt = 0;
        setState((current) => ({ ...current, streamStatus: "connecting" }));
      };
      socket.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data);
          setState((current) => applyStreamMessage(current, message, { symbol, perpSymbol, timeframe }));
        } catch {
          setState((current) => ({ ...current, streamStatus: "polling" }));
        }
      };
      socket.onerror = () => setState((current) => ({ ...current, streamStatus: "polling" }));
      socket.onclose = () => {
        if (cancelled) return;
        setState((current) => ({ ...current, streamStatus: "polling" }));
        const delay = Math.min(2000 * 2 ** reconnectAttempt, 15000);
        reconnectAttempt += 1;
        reconnectTimer = window.setTimeout(connect, delay);
      };
    };

    setState((current) => ({
      ...current,
      streamStatus: "connecting",
      feedStatus: { status: "connecting", source: "websocket" },
      latestKline: null,
    }));
    connect();

    return () => {
      cancelled = true;
      if (reconnectTimer) window.clearTimeout(reconnectTimer);
      if (socket) socket.close();
    };
  }, [symbol, perpSymbol, timeframe]);

  return { ...state, refresh };
}

function applyStreamMessage(current, message, selection) {
  const payload = message?.payload ?? {};
  if (message?.event === "exchange_snapshot") {
    const nextFeed = payload.feed_status ?? current.feedStatus;
    return {
      ...current,
      snapshot: payload.snapshot ?? current.snapshot,
      candles: payload.ohlcv?.candles ?? current.candles,
      candleSnapshotVersion: current.candleSnapshotVersion + 1,
      orderBook: payload.order_book ?? current.orderBook,
      trades: payload.trades ?? current.trades,
      feedStatus: nextFeed,
      streamStatus: streamStatusFromFeed(nextFeed),
      loading: false,
    };
  }
  if (message?.event === "kline") {
    const nextKline = payload;
    return {
      ...current,
      latestKline: nextKline,
      candles: upsertCandle(current.candles, nextKline),
      snapshot: {
        ...(current.snapshot ?? { symbol: selection.symbol, perp_symbol: selection.perpSymbol }),
        data_status: "ok",
        spot_last_price: nextKline.close,
        perp_last_price: nextKline.close,
        latest_bar_at: nextKline.time ?? nextKline.timestamp,
      },
      streamStatus: "live",
    };
  }
  if (message?.event === "order_book") {
    return { ...current, orderBook: payload, streamStatus: "live" };
  }
  if (message?.event === "trade") {
    const existing = Array.isArray(current.trades?.trades) ? current.trades.trades : [];
    return {
      ...current,
      trades: {
        symbol: selection.perpSymbol,
        exchange: "binance",
        data_status: "ok",
        source: payload.source ?? "binance_public_ws",
        trades: [payload, ...existing].slice(0, MAX_TRADES),
      },
      streamStatus: "live",
    };
  }
  if (message?.event === "feed_status") {
    return { ...current, feedStatus: payload, streamStatus: streamStatusFromFeed(payload) };
  }
  return current;
}

function upsertCandle(candles, nextCandle) {
  const time = nextCandle?.time ?? nextCandle?.timestamp;
  if (!time) return candles ?? [];
  const rows = [...(candles ?? [])];
  const index = rows.findIndex((item) => (item.time ?? item.timestamp) === time);
  if (index >= 0) rows[index] = { ...rows[index], ...nextCandle };
  else rows.push(nextCandle);
  return rows.slice(-300);
}

function streamStatusFromFeed(feedStatus) {
  if (feedStatus?.status === "live") return "live";
  if (feedStatus?.status === "rest_polling") return "polling";
  return "connecting";
}

function settledValue(result) {
  return result?.status === "fulfilled" ? result.value : null;
}
