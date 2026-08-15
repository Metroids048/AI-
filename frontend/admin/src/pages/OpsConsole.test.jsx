import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { OpsConsole } from "./OpsConsole";

const { request } = vi.hoisted(() => ({ request: vi.fn() }));

request.mockImplementation(async (path) => {
  if (path === "/api/v1/runtime/snapshot") return { exchange: { status: "available", value: {}, observed_at: "2026-08-11T00:00:00Z" }, scheduler: { status: "available", value: { running: false } } };
  if (path.startsWith("/api/v1/runtime/decisions")) return { items: [] };
  if (path.startsWith("/api/v1/runtime/exchange-orders")) return { items: [] };
  if (path === "/api/v1/runtime/positions") return { exchange: { status: "available", value: { positions: [], open_orders: [] } }, local: { value: [] } };
  if (path.startsWith("/api/v1/runtime/llm-invocations")) return { items: [] };
  if (path === "/api/v1/runtime/reconciliation") return { status: "healthy", entry_blocked_symbols: [] };
  if (path === "/api/v1/system/health/dependencies") return { status: "ok", dependencies: {} };
  if (path === "/api/v1/execution/trading-status") return { scheduler_running: false, live_feed_status: {} };
  if (path === "/api/v1/agents/tasks") return { items: [] };
  if (path.startsWith("/api/v1/market/news")) return { items: [] };
  if (path.startsWith("/api/v1/market/macro-events")) return { items: [] };
  if (path.startsWith("/api/v1/market-intelligence/signals")) return { provider_status: {} };
  if (path.startsWith("/api/v1/market-intelligence/refresh")) return { provider_status: {} };
  if (path.startsWith("/api/v1/notifications/outbox")) return { items: [] };
  if (path === "/api/v1/market/capabilities") {
    return {
      items: [
        { exchange: "binance", gateway_name: "spot", supports_market_data: true },
        { exchange: "binance", gateway_name: "usdt_perpetual", supports_order_submit: true },
      ],
    };
  }
  throw new Error(`unexpected path ${path}`);
});

vi.mock("../api/client", () => ({ request, streamUrl: (path) => `ws://localhost${path}` }));

afterEach(cleanup);

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <OpsConsole />
    </QueryClientProvider>,
  );
}

describe("OpsConsole", () => {
  it("refreshes intelligence only after an explicit action and keeps duplicate exchanges uniquely keyed", async () => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    renderPage();

    await screen.findByText("运维控制台");
    expect(await screen.findAllByText("binance")).toHaveLength(2);

    expect(request).not.toHaveBeenCalledWith(
      "/api/v1/market-intelligence/refresh?symbol=BTC/USDT",
      { method: "POST" },
    );

    fireEvent.click(screen.getByRole("button", { name: "刷新市场情报" }));

    expect(await screen.findByText("刷新市场情报")).toBeInTheDocument();
    expect(request).toHaveBeenCalledWith(
      "/api/v1/market-intelligence/refresh?symbol=BTC/USDT",
      { method: "POST" },
    );
    expect(consoleError).not.toHaveBeenCalled();
    consoleError.mockRestore();
  });

  it("keeps the complete Runtime Truth diagnostics on the operations page", async () => {
    renderPage();
    expect(await screen.findByText("运维控制台")).toBeInTheDocument();
    expect(await screen.findByText("Runtime Truth")).toBeInTheDocument();
    expect(screen.getByText("Exchange Orders")).toBeInTheDocument();
  });
});
