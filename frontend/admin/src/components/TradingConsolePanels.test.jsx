import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  MarketList,
  ModeBanner,
  OrderBookPanel,
  RecentTradesPanel,
  RuntimeControlPanel,
  TradingTicket,
} from "./TradingConsolePanels";

describe("Trading console panels", () => {
  it("renders Paper/Testnet runtime mode without exposing secrets", () => {
    render(
      <ModeBanner
        status={{
          mode: "paper",
          app_env: "development",
          binance_use_testnet: true,
          live_trading_enabled: false,
          credentials_configured: false,
          gateway_available: false,
        }}
      />,
    );

    expect(screen.getByText("Paper 模拟盘")).toBeInTheDocument();
    expect(screen.getByText("Testnet 已锁定")).toBeInTheDocument();
    expect(screen.queryByText(/api/i)).not.toBeInTheDocument();
  });

  it("renders top universe rows and lets user select a symbol", () => {
    const onSelect = vi.fn();
    render(
      <MarketList
        selectedSymbol="BTC/USDT"
        universe={[
          { symbol: "BTC/USDT", perp_symbol: "BTC/USDT:USDT", last_price: "61000", price_change_percent: 1.2 },
          { symbol: "ETH/USDT", perp_symbol: "ETH/USDT:USDT", last_price: "3200", price_change_percent: -0.7 },
        ]}
        onSelect={onSelect}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /ETH\/USDT/ }));

    expect(onSelect).toHaveBeenCalledWith("ETH/USDT", "ETH/USDT:USDT");
    expect(screen.getByText("24h 涨跌")).toBeInTheDocument();
  });

  it("requires validation evidence and stoploss before open orders", () => {
    const onAction = vi.fn();
    render(
      <TradingTicket
        symbol="BTC/USDT"
        mode="paper"
        latestPosition={{ symbol: "BTC/USDT", quantity: 0.01 }}
        onAction={onAction}
      />,
    );

    expect(screen.getByRole("button", { name: "开多" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Strategy ID"), { target: { value: "strategy-1" } });
    fireEvent.change(screen.getByLabelText("Backtest ID"), { target: { value: "backtest-1" } });
    fireEvent.change(screen.getByLabelText("数量"), { target: { value: "0.02" } });
    fireEvent.change(screen.getByLabelText("止损价"), { target: { value: "59000" } });
    fireEvent.click(screen.getByRole("button", { name: "开多" }));
    fireEvent.click(screen.getByRole("button", { name: "开空" }));
    fireEvent.click(screen.getByRole("button", { name: "平仓" }));
    fireEvent.click(screen.getByRole("button", { name: "调整杠杆" }));

    expect(onAction).toHaveBeenCalledWith("manualOrder", expect.objectContaining({ direction: "long" }));
    expect(onAction).toHaveBeenCalledWith("manualOrder", expect.objectContaining({ direction: "short" }));
    expect(onAction).toHaveBeenCalledWith("closePosition", expect.objectContaining({ symbol: "BTC/USDT" }));
    expect(onAction).toHaveBeenCalledWith("adjustLeverage", expect.objectContaining({ symbol: "BTC/USDT" }));
  });

  it("renders real order book and recent trades payloads", () => {
    render(
      <>
        <OrderBookPanel
          snapshot={{ spot_last_price: "61000" }}
          orderBook={{
            source: "binance_public_rest",
            bids: [{ price: "61000", quantity: "0.1", total: "0.1" }],
            asks: [{ price: "61010", quantity: "0.2", total: "0.2" }],
          }}
        />
        <RecentTradesPanel
          symbol="BTC/USDT"
          trades={{ source: "binance_public_rest", trades: [{ trade_id: "1", price: "61000", quantity: "0.01", side: "buy" }] }}
        />
      </>,
    );

    expect(screen.getByText("订单簿")).toBeInTheDocument();
    expect(screen.getByText("Binance 实时深度")).toBeInTheDocument();
    expect(screen.getByText("最新成交")).toBeInTheDocument();
    expect(screen.getByText("Binance 实时成交")).toBeInTheDocument();
  });

  it("runs the automatic paper cycle from the console control", () => {
    const onRunCycle = vi.fn();
    render(<RuntimeControlPanel streamStatus="live" onRunCycle={onRunCycle} />);

    fireEvent.click(screen.getByRole("button", { name: "运行一次自动开平仓 cycle" }));

    expect(screen.getByText("实时 K线已连接")).toBeInTheDocument();
    expect(onRunCycle).toHaveBeenCalledOnce();
  });
});
