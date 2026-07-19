import { describe, expect, it } from "vitest";

import { deskOrdersFromAccount, deskPositionsFromAccount } from "../pages/PaperConsole";

describe("desk Binance sync mappers", () => {
  it("maps exchange positions for the Positions tab", () => {
    const rows = deskPositionsFromAccount({
      connected: true,
      synced_at: "2026-07-12T12:00:00Z",
      positions: [
        {
          symbol: "BTC/USDT:USDT",
          side: "long",
          quantity: 0.0023,
          entry_price: 63965,
          mark_price: 63964,
          notional_usdt: 147.12,
          margin_usdt: 3.68,
          leverage: 40,
          unrealized_pnl: 0.01,
        },
      ],
    });
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({
      symbol: "BTC/USDT",
      side: "long",
      quantity: 0.0023,
      notional_usdt: 147.12,
      margin_usdt: 3.68,
      leverage: 40,
      source: "binance_exchange",
    });
  });

  it("prefers open orders ahead of recent fills", () => {
    const rows = deskOrdersFromAccount({
      connected: true,
      synced_at: "2026-07-12T12:00:00Z",
      open_orders: [
        {
          order_id: "1",
          symbol: "BTCUSDT",
          side: "SELL",
          order_type: "STOP_MARKET",
          status: "NEW",
          quantity: 0.0023,
          updated_at: "2026-07-12T12:01:02Z",
        },
      ],
      recent_orders: [
        {
          order_id: "1",
          symbol: "BTCUSDT",
          side: "BUY",
          order_type: "MARKET",
          status: "FILLED",
          quantity: 0.0023,
          updated_at: "2026-07-12T12:01:01Z",
        },
        {
          order_id: "2",
          symbol: "ETHUSDT",
          side: "BUY",
          order_type: "MARKET",
          status: "FILLED",
          quantity: 0.05,
          updated_at: "2026-07-12T12:01:00Z",
        },
      ],
    });
    expect(rows.map((row) => row.gateway_order_id)).toEqual(["1", "2"]);
    expect(rows[0].entry_context.execution_kind).toBe("binance_open_order");
    expect(rows[0].created_at).toBe("2026-07-12T12:01:02Z");
  });
});
