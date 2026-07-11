import { renderHook } from "@testing-library/react";
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
  it("does not poll the Binance account in local API-only mode", async () => {
    vi.stubEnv("VITE_LOCAL_CONSOLE_API_ONLY", "true");
    request.mockResolvedValue(null);
    const { useConsoleData } = await import("./useConsoleData");

    renderHook(() => useConsoleData("BTC/USDT", "BTC/USDT:USDT", "1m"));

    expect(request).not.toHaveBeenCalledWith("/api/v1/execution/binance-testnet-account");
  });
});
