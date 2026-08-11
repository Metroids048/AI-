import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const request = vi.fn();

vi.mock("../api/client", () => ({
  request,
  streamUrl: (path) => `ws://localhost${path}`,
}));

afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
  request.mockReset();
});

describe("useConsoleData", () => {
  it("does not probe the legacy Binance account endpoint", async () => {
    vi.stubEnv("VITE_LOCAL_CONSOLE_API_ONLY", "true");
    request.mockResolvedValue(null);
    const { useConsoleData } = await import("./useConsoleData");

    renderHook(() => useConsoleData("BTC/USDT", "BTC/USDT:USDT", "1m"));

    expect(request).not.toHaveBeenCalledWith("/api/v1/execution/binance-testnet-account");
  });

  it("keeps one refresh in flight for the same selection", async () => {
    vi.stubEnv("VITE_LOCAL_CONSOLE_API_ONLY", "true");
    request.mockImplementation(() => new Promise(() => {}));
    const { useConsoleData } = await import("./useConsoleData");
    const { result } = renderHook(() => useConsoleData("BTC/USDT", "BTC/USDT:USDT", "1m"));

    let first;
    let second;
    await act(async () => {
      first = result.current.refresh();
      await Promise.resolve();
      second = result.current.refresh();
    });

    expect(request).toHaveBeenCalled();
    expect(second).toBe(first);
  });

  it("aborts the previous selection request when the symbol changes", async () => {
    vi.stubEnv("VITE_LOCAL_CONSOLE_API_ONLY", "true");
    request.mockImplementation((_path, options = {}) => new Promise((resolve, reject) => {
      options.signal?.addEventListener("abort", () => {
        const error = new Error("aborted");
        error.name = "AbortError";
        reject(error);
      });
    }));
    const { useConsoleData } = await import("./useConsoleData");
    const { result, rerender } = renderHook(
      ({ symbol }) => useConsoleData(symbol, `${symbol}:USDT`, "1m"),
      { initialProps: { symbol: "BTC/USDT" } },
    );

    await act(async () => {
      result.current.refresh();
      await Promise.resolve();
    });
    rerender({ symbol: "ETH/USDT" });

    expect(request.mock.calls.some(([, options]) => options?.signal?.aborted)).toBe(true);
  });
});
