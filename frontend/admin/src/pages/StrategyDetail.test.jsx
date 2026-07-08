import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";

import { StrategyDetail } from "./StrategyDetail";

vi.mock("../api/client", () => ({
  request: vi.fn(async (path) => {
    if (path === "/api/v1/strategies/s-1") {
      return {
        strategy_id: "s-1",
        strategy_key: "BTC_Trend",
        core_thesis: "trend follow",
        backtest_status: "passed",
        paper_status: "running",
        live_status: "not_started",
        rules: {},
        failure_reasons: [],
        iteration_history: [],
      };
    }
    if (path.startsWith("/api/v1/strategies/versions")) return { items: [{ version_id: "v1", version_label: "v1", change_summary: "init" }] };
    throw new Error(`unexpected path ${path}`);
  }),
}));

describe("StrategyDetail", () => {
  it("renders strategy contract summary", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={["/strategies/s-1"]}>
          <Routes>
            <Route path="/strategies/:strategyId" element={<StrategyDetail />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(await screen.findByText("BTC_Trend")).toBeTruthy();
    expect(await screen.findByText("trend follow")).toBeTruthy();
    expect(await screen.findByText("init")).toBeTruthy();
  });
});
