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
  it("always probes Binance Demo account so Positions/Orders stay exchange-synced", async () => {
    vi.stubEnv("VITE_LOCAL_CONSOLE_API_ONLY", "true");
    request.mockResolvedValue(null);
    const { useConsoleData } = await import("./useConsoleData");

    renderHook(() => useConsoleData("BTC/USDT", "BTC/USDT:USDT", "1m"));

    expect(request).toHaveBeenCalledWith("/api/v1/execution/binance-testnet-account");
  });
});
