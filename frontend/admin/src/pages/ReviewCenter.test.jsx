import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ReviewCenter } from "./ReviewCenter";

const { request } = vi.hoisted(() => ({ request: vi.fn() }));

vi.mock("../api/client", () => ({
  request,
  streamUrl: (path) => `ws://localhost${path}`,
}));

request.mockImplementation(async (path) => {
  if (path === "/api/v1/runtime/snapshot") return { exchange: { status: "available", observed_at: "2026-08-11T00:00:00Z", value: { positions: [], open_orders: [], account: { wallet_balance: 10000 } } } };
  if (path.startsWith("/api/v1/runtime/decisions")) return { items: [{ symbol: "BTC/USDT", terminal_reason: "技术信号不足" }] };
  if (path.startsWith("/api/v1/runtime/exchange-orders")) return { items: [] };
  if (path === "/api/v1/runtime/positions") return { exchange: { status: "available", value: { positions: [], open_orders: [] } }, local: { value: [] } };
  if (path.startsWith("/api/v1/runtime/llm-invocations")) return { items: [] };
  if (path === "/api/v1/runtime/reconciliation") return { status: "healthy", entry_blocked_symbols: [] };
  if (path === "/api/v1/reviews") return { items: [] };
  if (path.startsWith("/api/v1/failures")) return { items: [] };
  if (path === "/api/v1/decision-memory") return { items: [] };
  if (path.startsWith("/api/v1/market/news")) return { items: [] };
  if (path.startsWith("/api/v1/market-intelligence/signals")) return {};
  throw new Error(`unexpected path ${path}`);
});

describe("ReviewCenter", () => {
  it("shows current runtime decisions when historical review tables are empty", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><ReviewCenter /></QueryClientProvider>);

    expect(await screen.findByText("当前自动交易决策")).toBeInTheDocument();
    expect(await screen.findByText(/BTC\/USDT/)).toBeInTheDocument();
    await waitFor(() => expect(request).toHaveBeenCalledWith("/api/v1/market/news?limit=12&refresh=false"));
    expect(request).not.toHaveBeenCalledWith("/api/v1/market/news?limit=12&refresh=true");
  });
});
