/**
 * Tests for useAutomatedTradingRuntime hook (Task 14).
 */
import { renderHook, waitFor } from "@testing-library/react";
import { vi, describe, it, expect, beforeEach, afterEach } from "vitest";

import { useAutomatedTradingRuntime } from "./useAutomatedTradingRuntime";
import * as api from "../api/automatedTrading";

vi.mock("../api/automatedTrading");

const MOCK_SNAPSHOT = {
  engine: {
    engine_id: "automated-trading-v2",
    mode: "BINANCE_TESTNET",
    activation: "ACTIVE",
    entry_enabled: true,
    mainnet_supported: false,
  },
  scheduler: { running: true },
  exchange: { available: true, positions: [], freshness: "FRESH" },
  local_projection: { source: "V2_LOCAL_PROJECTION", positions: [] },
  reconciliation: { status: "HEALTHY", mismatches: [] },
  latest_decisions: [],
  latest_incidents: [],
  latest_llm_invocation: null,
};

describe("useAutomatedTradingRuntime", () => {
  beforeEach(() => {
    api.fetchRuntimeSnapshot.mockResolvedValue(MOCK_SNAPSHOT);
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("starts in loading state", () => {
    const { result } = renderHook(() => useAutomatedTradingRuntime(10_000));
    expect(result.current.loading).toBe(true);
    expect(result.current.snapshot).toBeNull();
  });

  it("populates snapshot after first fetch", async () => {
    const { result } = renderHook(() => useAutomatedTradingRuntime(10_000));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.snapshot).toEqual(MOCK_SNAPSHOT);
    expect(result.current.error).toBeNull();
  });

  it("records lastSuccessAt on success", async () => {
    const { result } = renderHook(() => useAutomatedTradingRuntime(10_000));
    await waitFor(() => expect(result.current.lastSuccessAt).not.toBeNull());
  });

  it("sets error on fetch failure", async () => {
    api.fetchRuntimeSnapshot.mockRejectedValueOnce(new Error("network error"));
    const { result } = renderHook(() => useAutomatedTradingRuntime(10_000));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBe("network error");
  });

  it("keeps previous snapshot on error (shows last-seen)", async () => {
    const { result, rerender } = renderHook(() => useAutomatedTradingRuntime(10_000));
    await waitFor(() => expect(result.current.snapshot).not.toBeNull());

    // Simulate second fetch failing
    api.fetchRuntimeSnapshot.mockRejectedValueOnce(new Error("timeout"));

    // Trigger re-fetch by rerendering
    rerender();

    // Wait a bit for the fetch to happen
    await new Promise(resolve => setTimeout(resolve, 100));

    // Snapshot from first call is still present
    expect(result.current.snapshot).toEqual(MOCK_SNAPSHOT);
  });

  it("calls API on mount", async () => {
    renderHook(() => useAutomatedTradingRuntime(10_000));
    await waitFor(() => expect(api.fetchRuntimeSnapshot).toHaveBeenCalledTimes(1));
  });

  it("refresh triggers immediate fetch", async () => {
    const { result } = renderHook(() => useAutomatedTradingRuntime(10_000));
    await waitFor(() => expect(api.fetchRuntimeSnapshot).toHaveBeenCalledTimes(1));

    result.current.refresh();
    await waitFor(() => expect(api.fetchRuntimeSnapshot).toHaveBeenCalledTimes(2));
  });
});
