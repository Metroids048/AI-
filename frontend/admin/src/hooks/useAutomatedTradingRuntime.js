/**
 * useAutomatedTradingRuntime — V2 runtime truth hook (plan section 14).
 *
 * Replaces the legacy multi-API guessing in useConsoleData.
 * Polls /api/v2/automated-trading/runtime on a fixed interval.
 *
 * Design rules (plan 14.3):
 * - Never substitute ?? 0 for unknown balances, prices, or PnL.
 * - API unavailable → show "unavailable/last-seen", not a stale value.
 * - No Testnet Mirror Toggle.
 * - No paper_run_id guessing.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { fetchRuntimeSnapshot } from "../api/automatedTrading";

const DEFAULT_POLL_MS = 10_000; // 10 s

const INITIAL_STATE = {
  snapshot: null,
  loading: true,
  error: null,
  lastSuccessAt: null,
};

export function useAutomatedTradingRuntime(pollMs = DEFAULT_POLL_MS) {
  const [state, setState] = useState(INITIAL_STATE);
  const timerRef = useRef(null);
  const mountedRef = useRef(true);

  const fetchOnce = useCallback(async () => {
    try {
      const snapshot = await fetchRuntimeSnapshot();
      if (!mountedRef.current) return;
      setState({
        snapshot,
        loading: false,
        error: null,
        lastSuccessAt: new Date().toISOString(),
      });
    } catch (err) {
      if (!mountedRef.current) return;
      setState((prev) => ({
        ...prev,
        loading: false,
        // Keep the last snapshot so the UI can show "last seen at X"
        // but set error so the component knows data may be stale.
        error: err?.message ?? "API unavailable",
      }));
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    fetchOnce();
    timerRef.current = setInterval(fetchOnce, pollMs);
    return () => {
      mountedRef.current = false;
      clearInterval(timerRef.current);
    };
  }, [fetchOnce, pollMs]);

  const refresh = useCallback(() => {
    clearInterval(timerRef.current);
    fetchOnce();
    timerRef.current = setInterval(fetchOnce, pollMs);
  }, [fetchOnce, pollMs]);

  return {
    snapshot: state.snapshot,
    loading: state.loading,
    error: state.error,
    lastSuccessAt: state.lastSuccessAt,
    refresh,
  };
}
