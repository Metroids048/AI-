import { describe, expect, it } from "vitest";

import {
  deskOrdersFromAccount,
  deskOrdersFromRuntimeTruth,
  deskPositionsFromAccount,
  deskPositionsFromRuntimeTruth,
} from "../pages/PaperConsole";

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

  it("does not leak legacy/account positions when V2 exchange truth is available and flat", () => {
    const rows = deskPositionsFromRuntimeTruth(
      { exchange: { available: true, positions: [] } },
      {
        connected: true,
        positions: [{ symbol: "SOL/USDT:USDT", side: "short", quantity: 29 }],
      },
    );

    expect(rows).toEqual([]);
  });

  it("maps V2 exchange positions and orders into the main desk", () => {
    const positions = deskPositionsFromRuntimeTruth({
      exchange: {
        available: true,
        observed_at: "2026-07-29T09:30:05Z",
        positions: [
          {
            symbol: "ETH/USDT",
            direction: "long",
            quantity: "0.05",
            entry_price: "1900",
            mark_price: "1910",
            leverage: 40,
          },
        ],
      },
    });
    const orders = deskOrdersFromRuntimeTruth({
      exchange: {
        available: true,
        timestamp: "2026-07-29T09:30:05Z",
        open_orders: [
          {
            exchange_order_id: "stop-1",
            symbol: "ETH/USDT",
            side: "sell",
            order_type: "stop_market",
            status: "new",
            quantity: "0.05",
            reduce_only: true,
          },
        ],
      },
    });

    expect(positions[0]).toMatchObject({
      symbol: "ETH/USDT",
      side: "long",
      quantity: "0.05",
      leverage: 40,
      source: "binance_v2_reconciliation",
    });
    expect(orders[0]).toMatchObject({
      gateway_order_id: "stop-1",
      symbol: "ETH/USDT",
      execution_status: "new",
      gateway_name: "binance_usdt_perpetual",
    });
  });

  it("maps raw reconciliation contracts and CCXT order ids", () => {
    const positions = deskPositionsFromRuntimeTruth({
      exchange: { status: "available", observed_at: "2026-08-11T00:00:00Z", value: {
        positions: [{ symbol: "BTC/USDT:USDT", contracts: 0.01, side: "long", entry_price: 100, mark_price: 101, leverage: 5 }],
      } },
    });
    const orders = deskOrdersFromRuntimeTruth({
      exchange: { status: "available", observed_at: "2026-08-11T00:00:00Z", value: {
        open_orders: [{ id: "ccxt-1", symbol: "BTC/USDT:USDT", side: "sell", type: "limit", status: "open", amount: 0.01, price: 102 }],
      } },
    });
    expect(positions[0]).toMatchObject({ quantity: 0.01, side: "long", symbol: "BTC/USDT" });
    expect(orders[0]).toMatchObject({ gateway_order_id: "ccxt-1", symbol: "BTC/USDT" });
  });

  it("keeps external manual ownership visible and out of strategy attribution", () => {
    const positions = deskPositionsFromRuntimeTruth({
      exchange: {
        status: "available",
        observed_at: "2026-08-18T00:00:00Z",
        value: {
          positions: [{ symbol: "BTC/USDT", contracts: 0.5, side: "short", mark_price: 64000 }],
        },
      },
      reconciliation: {
        value: {
          mismatch: {
            value: {
              ownership_positions: [{
                symbol: "BTC/USDT",
                side: "short",
                exchange_quantity: 0.5,
                external_manual_quantity: 0.5,
                managed_quantity: 0,
                ownership: "EXTERNAL_MANUAL",
              }],
            },
          },
        },
      },
    });

    expect(positions[0]).toMatchObject({
      ownership: "EXTERNAL_MANUAL",
      source: "binance_external_manual",
      strategy_performance_eligible: false,
      external_manual_quantity: 0.5,
      managed_quantity: 0,
    });
  });
});
