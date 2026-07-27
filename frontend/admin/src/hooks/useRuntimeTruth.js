import { useCallback, useEffect, useRef, useState } from "react";

import { request, streamUrl } from "../api/client";

const FALLBACK_INTERVAL_MS = 30_000;

function unavailableDatum(source, error) {
  return {
    value: null,
    source,
    observed_at: new Date().toISOString(),
    freshness: "unavailable",
    status: "unavailable",
    error,
  };
}

export function useRuntimeTruth(symbol) {
  const [state, setState] = useState({
    snapshot: null,
    decisions: [],
    exchangeOrders: [],
    positions: null,
    llmInvocations: [],
    reconciliation: null,
    streamStatus: "connecting",
    lastSuccessAt: null,
    error: "",
  });
  const mounted = useRef(true);
  const refreshInFlight = useRef(false);

  const refresh = useCallback(async () => {
    if (refreshInFlight.current) return;
    refreshInFlight.current = true;
    try {
      const symbolQuery = symbol ? `?symbol=${encodeURIComponent(symbol)}&limit=100` : "?limit=100";
      const results = await Promise.allSettled([
        request("/api/v1/runtime/snapshot"),
        request(`/api/v1/runtime/decisions${symbolQuery}`),
        request(`/api/v1/runtime/exchange-orders${symbolQuery}`),
        request("/api/v1/runtime/positions"),
        request(`/api/v1/runtime/llm-invocations${symbolQuery}`),
        request("/api/v1/runtime/reconciliation"),
      ]);
      if (!mounted.current) return;
      const value = (index) => (results[index].status === "fulfilled" ? results[index].value : null);
      const failureMessage = (index) => results[index].reason?.message ?? "数据不可用";
      const firstFailure = results.find((result) => result.status === "rejected");
      setState((current) => ({
        ...current,
        snapshot: value(0) ?? {
          exchange: unavailableDatum("BINANCE_USDT_M_TESTNET", failureMessage(0)),
          local_projection: unavailableDatum("LOCAL_SQL_PROJECTION", failureMessage(0)),
          mismatch: unavailableDatum("RUNTIME_RECONCILIATION", failureMessage(0)),
          scheduler: unavailableDatum("RUNTIME_SCHEDULER_HEARTBEAT", failureMessage(0)),
        },
        decisions: value(1)?.items ?? [],
        exchangeOrders: value(2)?.items ?? [],
        positions: value(3) ?? {
          exchange: unavailableDatum("BINANCE_USDT_M_TESTNET", failureMessage(3)),
          local: unavailableDatum("LOCAL_SQL_PROJECTION", failureMessage(3)),
        },
        llmInvocations: value(4)?.items ?? [],
        reconciliation: value(5) ?? {
          status: "unavailable",
          entry_blocked_symbols: ["BTC/USDT", "ETH/USDT"],
          error: failureMessage(5),
        },
        lastSuccessAt: results.some((result) => result.status === "fulfilled")
          ? new Date().toISOString()
          : current.lastSuccessAt,
        error: firstFailure?.reason?.message ?? "",
      }));
    } finally {
      refreshInFlight.current = false;
    }
  }, [symbol]);

  useEffect(() => {
    mounted.current = true;
    refresh();
    const timer = window.setInterval(refresh, FALLBACK_INTERVAL_MS);
    return () => {
      mounted.current = false;
      window.clearInterval(timer);
    };
  }, [refresh]);

  useEffect(() => {
    if (!("WebSocket" in window)) {
      setState((current) => ({ ...current, streamStatus: "polling" }));
      return undefined;
    }
    let cancelled = false;
    let socket;
    let reconnectTimer;
    let attempt = 0;
    const connect = () => {
      if (cancelled) return;
      socket = new WebSocket(streamUrl("/api/v1/runtime/events"));
      socket.onopen = () => {
        attempt = 0;
        setState((current) => ({ ...current, streamStatus: "live" }));
      };
      socket.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data);
          if (payload.event !== "runtime_truth") return;
          setState((current) => ({
            ...current,
            decisions: payload.decisions ?? current.decisions,
            exchangeOrders: payload.exchange_orders ?? current.exchangeOrders,
            llmInvocations: payload.llm_invocations ?? current.llmInvocations,
            lastSuccessAt: payload.observed_at ?? new Date().toISOString(),
            streamStatus: "live",
          }));
        } catch {
          setState((current) => ({ ...current, streamStatus: "polling" }));
        }
      };
      socket.onerror = () => setState((current) => ({ ...current, streamStatus: "polling" }));
      socket.onclose = () => {
        if (cancelled) return;
        setState((current) => ({ ...current, streamStatus: "polling" }));
        const delay = Math.min(2_000 * 2 ** attempt, 15_000);
        attempt += 1;
        reconnectTimer = window.setTimeout(connect, delay);
      };
    };
    connect();
    return () => {
      cancelled = true;
      if (reconnectTimer) window.clearTimeout(reconnectTimer);
      if (socket?.readyState === WebSocket.CONNECTING) {
        socket.onopen = () => socket.close();
        socket.onerror = null;
        socket.onclose = null;
      } else if (socket?.readyState === WebSocket.OPEN) {
        socket.close();
      }
    };
  }, []);

  return { ...state, refresh };
}

export { FALLBACK_INTERVAL_MS };
