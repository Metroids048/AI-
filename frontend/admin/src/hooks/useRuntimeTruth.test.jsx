import { act, render, renderHook, waitFor } from "@testing-library/react";
import { createElement } from "react";
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

  // T-002: Trading -> Review -> Trading must show the last real account value on
  // the first returning frame, not a placeholder. Navigation swaps the Outlet
  // child while RuntimeTruthProvider stays mounted above it, so the state that
  // must survive lives in the provider — this test keeps the provider mounted and
  // remounts only the consumer, which is what routing actually does.
  it("retains the last runtime account value when only the page consumer remounts", async () => {
    const account = {
      wallet_balance: 10000,
      available_balance: 9000,
      margin_balance: 10000,
      unrealized_pnl: 0,
      open_position_count: 0,
    };
    queueRefresh({
      snapshot: {
        exchange: {
          status: "available",
          observed_at: "2026-08-12T02:00:00Z",
          value: { account },
        },
      },
    });

    const { RuntimeTruthProvider, useRuntimeTruth } = await import("./useRuntimeTruth");

    // A page-level consumer. Mounting/unmounting this component is what route
    // navigation does; the hook itself is always called unconditionally.
    let observed = null;
    function TradingPage() {
      observed = useRuntimeTruth("BTC/USDT");
      return null;
    }

    function Shell({ pageMounted }) {
      return createElement(
        RuntimeTruthProvider,
        null,
        pageMounted ? createElement(TradingPage) : null,
      );
    }

    const { rerender } = render(createElement(Shell, { pageMounted: true }));

    await waitFor(() =>
      expect(observed.snapshot.exchange.value.account.wallet_balance).toBe(10000),
    );

    // Navigate away (Trading -> Review): the page unmounts, provider stays.
    rerender(createElement(Shell, { pageMounted: false }));

    // Any refresh triggered by returning is still in flight.
    request.mockImplementation(() => new Promise(() => {}));

    // Navigate back: the first frame must still carry the last real value.
    rerender(createElement(Shell, { pageMounted: true }));

    const firstFrame = observed.snapshot.exchange;
    expect(firstFrame.value.account.wallet_balance).toBe(10000);
    expect(firstFrame.status).toBe("available");
    expect(firstFrame.observed_at).toBe("2026-08-12T02:00:00Z");
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
