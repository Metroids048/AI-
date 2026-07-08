import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  AutoEngineStatusBadge,
  MarketList,
  ModeBanner,
  OrderBookPanel,
  RecentTradesPanel,
  RuntimeControlPanel,
  TradingTicket,
} from "./TradingConsolePanels";

const manualContext = {
  strategy_id: "strategy-manual",
  validation_backtest_run_id: "backtest-manual",
  paper_run_id: "paper-manual",
  warning: "Paper-only sandbox evidence; not eligible for Testnet or Live promotion.",
};

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
    expect(screen.getByText("24h")).toBeInTheDocument();
  });

  it("uses automatic Paper evidence and stoploss before open orders", () => {
    const onAction = vi.fn();
    render(
      <TradingTicket
        symbol="BTC/USDT"
        timeframe="1m"
        mode="paper"
        manualContext={manualContext}
        latestPrice={61000}
        latestPosition={{ symbol: "BTC/USDT", quantity: 0.01 }}
        onAction={onAction}
      />,
    );

    fireEvent.change(screen.getByLabelText("数量"), { target: { value: "0.02" } });
    fireEvent.click(screen.getByRole("button", { name: "限价" }));
    fireEvent.change(screen.getByLabelText("限价"), { target: { value: "60000" } });
    fireEvent.change(screen.getByLabelText("止损"), { target: { value: "59000" } });
    fireEvent.click(screen.getByRole("button", { name: "开多" }));
    fireEvent.click(screen.getByRole("button", { name: "开空" }));
    fireEvent.click(screen.getByRole("button", { name: "平仓" }));
    fireEvent.click(screen.getByRole("button", { name: "调杠杆" }));

    expect(onAction).toHaveBeenCalledWith(
      "manualOrder",
      expect.objectContaining({
        direction: "long",
        order_type: "limit",
        limit_price: 60000,
        time_in_force: "GTC",
        strategy_id: "strategy-manual",
        validation_backtest_run_id: "backtest-manual",
        timeframe: "1m",
      }),
    );
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
            source: "binance_public_ws",
            bids: [{ price: "61000", quantity: "0.1", total: "0.1" }],
            asks: [{ price: "61010", quantity: "0.2", total: "0.2" }],
          }}
        />
        <RecentTradesPanel
          symbol="BTC/USDT:USDT"
          trades={{ source: "binance_public_ws", trades: [{ trade_id: "1", price: "61000", quantity: "0.01", side: "buy" }] }}
        />
      </>,
    );

    expect(screen.getByText("盘口")).toBeInTheDocument();
    expect(screen.getAllByText("Binance WS")).toHaveLength(2);
    expect(screen.getByText("最新成交")).toBeInTheDocument();
  });

  it("runs the automatic paper cycle from the console control", () => {
    const onRunCycle = vi.fn();
    render(<RuntimeControlPanel streamStatus="live" onRunCycle={onRunCycle} />);

    fireEvent.click(screen.getByRole("button", { name: "运行一次自动开平仓 cycle" }));

    expect(screen.getByText("实时行情已连接")).toBeInTheDocument();
    expect(onRunCycle).toHaveBeenCalledOnce();
  });

  it("exposes the explicit Binance Testnet mirror toggle", () => {
    const onMirrorToggle = vi.fn();
    const view = render(
      <RuntimeControlPanel
        streamStatus="live"
        mirrorToGateway={false}
        onMirrorToggle={onMirrorToggle}
        onRunCycle={vi.fn()}
      />,
    );

    const panel = within(view.container);
    fireEvent.click(panel.getByRole("button", { name: "开启 Testnet 镜像" }));

    expect(panel.getByText("本地 Paper 成交优先；开启后才额外镜像到 Binance Futures Testnet。")).toBeInTheDocument();
    expect(onMirrorToggle).toHaveBeenCalledWith(true);
  });

  it("renders automatic engine scheduler status", () => {
    render(<AutoEngineStatusBadge status={{ scheduler_running: true, scheduler_mode: "inprocess", next_cycle_eta_seconds: 42 }} />);

    expect(screen.getByText("自动运行中")).toBeInTheDocument();
    expect(screen.getByText("inprocess / 下次 00:42")).toBeInTheDocument();
  });
});
