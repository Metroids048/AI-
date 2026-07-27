import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const request = vi.fn();

vi.mock("../api/client", () => ({
  request,
  streamUrl: (path) => `ws://localhost${path}`,
}));

class PendingWebSocket {
  static CONNECTING = 0;
  static OPEN = 1;

  constructor() {
    this.readyState = PendingWebSocket.CONNECTING;
  }

  close() {}
}

function queueRefresh({
  snapshot = { exchange: { status: "available" } },
  decisions = { items: [] },
  exchangeOrders = { items: [] },
  positions = { exchange: { status: "available" }, local: { value: [] } },
  llmInvocations = { items: [] },
  reconciliation = { status: "healthy" },
} = {}) {
  for (const value of [
    snapshot,
    decisions,
    exchangeOrders,
    positions,
    llmInvocations,
    reconciliation,
  ]) {
    if (value instanceof Error) request.mockRejectedValueOnce(value);
    else request.mockResolvedValueOnce(value);
  }
}

beforeEach(() => {
  vi.stubGlobal("WebSocket", PendingWebSocket);
});

afterEach(() => {
  vi.unstubAllGlobals();
  request.mockReset();
});

describe("useRuntimeTruth", () => {
  it("does not overlap 30-second fallback refreshes while exchange truth is still loading", async () => {
    request.mockImplementation(() => new Promise(() => {}));
    const { useRuntimeTruth } = await import("./useRuntimeTruth");
    const { result } = renderHook(() => useRuntimeTruth("BTC/USDT"));
    await waitFor(() => expect(request).toHaveBeenCalledTimes(6));

    await act(async () => {
      await result.current.refresh();
    });

    expect(request).toHaveBeenCalledTimes(6);
  });

  it("replaces a failed endpoint with explicit unavailable truth instead of stale data", async () => {
    queueRefresh({
      positions: {
        exchange: { status: "available", value: [{ symbol: "BTC/USDT", contracts: 1 }] },
        local: { status: "available", value: [] },
      },
    });
    const { useRuntimeTruth } = await import("./useRuntimeTruth");
    const { result } = renderHook(() => useRuntimeTruth("BTC/USDT"));

    await waitFor(() => expect(result.current.positions.exchange.status).toBe("available"));

    queueRefresh({ positions: new Error("position endpoint timeout") });
    await act(async () => {
      await result.current.refresh();
    });

    expect(result.current.positions.exchange.status).toBe("unavailable");
    expect(result.current.positions.exchange.value).toBeNull();
    expect(result.current.positions.exchange.error).toBe("position endpoint timeout");
  });
});
