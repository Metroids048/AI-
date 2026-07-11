import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";

import { ValidationCenter } from "./ValidationCenter";

vi.mock("../api/client", () => ({
  request: vi.fn(async (path) => {
    if (path === "/api/v1/backtests") return { items: [{ backtest_run_id: "bt-1", run_status: "done", strategy_id: "s1", metrics_summary: { sharpe: 1.2 } }] };
    if (path === "/api/v1/optimizations") return { items: [] };
    if (path === "/api/v1/strategies") return { items: [{ strategy_id: "s1", strategy_key: "BTC_Trend" }] };
    if (path === "/api/v1/validation/hypotheses") return { items: [] };
    if (path.startsWith("/api/v1/market/funding-arbitrage-signal")) return { source: "binance", signal_status: "pending" };
    throw new Error(`unexpected path ${path}`);
  }),
}));

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ValidationCenter />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("ValidationCenter", () => {
  it("renders backtest rows and submit form", async () => {
    renderPage();
    expect(await screen.findByText("验证中心")).toBeTruthy();
    expect(await screen.findByText("bt-1")).toBeTruthy();
    expect(screen.getByRole("combobox", { name: "回测策略" })).toBeTruthy();
  });
});
