import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  AutoEngineStatusBadge,
  ExchangePositionHint,
  MarketList,
  ModeBanner,
  OrdersTable,
  RecentTradesPanel,
  RuntimeControlPanel,
  TestnetAccountPanel,
  TradingTicket,
} from "./TradingConsolePanels";
import { AppShell } from "./Common";
import { AutoSettingsPanel, OrderSyncPanel } from "./RuntimePanels";

afterEach(() => {
  cleanup();
});

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
        account={{
          api_backend: "testnet-fallback",
          web_ui_url: "https://testnet.binancefuture.com/en/futures/ETHUSDT",
        }}
      />,
    );

    expect(screen.getByText("本地模拟盘")).toBeInTheDocument();
    expect(screen.getByText("币安模拟盘已锁定")).toBeInTheDocument();
    expect(screen.getByText(/主网 futures\.binance\.com 不同步/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "testnet.binancefuture.com" })).toBeInTheDocument();
    expect(screen.queryByText(/secret|token|key/i)).not.toBeInTheDocument();
  });

  it("prompts operator to jump to exchange position symbol", () => {
    const onSelect = vi.fn();
    render(
      <ExchangePositionHint
        selectedSymbol="BTC/USDT"
        positions={[{ symbol: "ETH/USDT", side: "long", quantity: 0.055 }]}
        onSelect={onSelect}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /ETH\/USDT/ }));
    expect(onSelect).toHaveBeenCalledWith("ETH/USDT", "ETH/USDT:USDT");
  });

  it("renders top universe rows and lets user select a symbol", () => {
    const onSelect = vi.fn();
    const view = render(
      <MarketList
        selectedSymbol="BTC/USDT"
        universe={[
          { symbol: "BTC/USDT", perp_symbol: "BTC/USDT:USDT", last_price: "61000", price_change_percent: 1.2 },
          { symbol: "ETH/USDT", perp_symbol: "ETH/USDT:USDT", last_price: "3200", price_change_percent: -0.7 },
        ]}
        onSelect={onSelect}
      />,
    );

    fireEvent.click(within(view.container).getByRole("button", { name: /ETH\/USDT/ }));

    expect(onSelect).toHaveBeenCalledWith("ETH/USDT", "ETH/USDT:USDT");
    expect(within(view.container).getByText("24h")).toBeInTheDocument();
  });

  it("keeps the Testnet account panel explicit when automatic polling is paused", () => {
    render(<TestnetAccountPanel account={null} />);

    expect(screen.getByText("按需探测")).toBeInTheDocument();
  });

  it("shows an explicit loading state instead of a fake three-symbol universe", () => {
    const view = render(<MarketList selectedSymbol="BTC/USDT" universe={[]} onSelect={vi.fn()} />);
    const panel = within(view.container);

    expect(panel.getByText("正在加载固定 Top20 市场列表")).toBeInTheDocument();
    expect(panel.queryByRole("button", { name: /ETH\/USDT/ })).not.toBeInTheDocument();
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

  it("renders real recent trades payloads without an order book panel", () => {
    render(
      <RecentTradesPanel
        symbol="BTC/USDT:USDT"
        trades={{ source: "binance_public_ws", trades: [{ trade_id: "1", price: "61000", quantity: "0.01", side: "buy" }] }}
      />,
    );

    expect(screen.queryByText("盘口")).not.toBeInTheDocument();
    expect(screen.getByText("币安 WS")).toBeInTheDocument();
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

    expect(panel.getByText(/先提交币安/)).toBeInTheDocument();
    expect(panel.getByText(/成功才本地成交/)).toBeInTheDocument();
    expect(onMirrorToggle).toHaveBeenCalledWith(true);
  });

  it("renders automatic engine scheduler status", () => {
    render(
      <AutoEngineStatusBadge
        status={{
          scheduler_running: true,
          scheduler_mode: "inprocess",
          next_cycle_eta_seconds: 42,
          auto_execution_state: "armed",
          fixed_top20_count: 20,
          active_execution_count: 3,
          market_data_coverage_count: 3,
          active_execution_symbols: ["BTC/USDT", "ETH/USDT", "SOL/USDT"],
        }}
      />,
    );

    expect(screen.getByText("自动运行中")).toBeInTheDocument();
    expect(screen.getByText("inprocess / 下次 00:42")).toBeInTheDocument();
    expect(screen.getByText("Mock 自动下单已武装 / BTC、ETH、SOL / 数据 3/3")).toBeInTheDocument();
  });

  it("shows matched and unmatched Binance order reconciliation", () => {
    const view = render(
      <OrderSyncPanel
        orderSync={{
          execution_mode: "binance_simulation_first",
          local_orders: [],
          gateway_recent_orders: [],
          positions: [],
          protection_order_refs: [],
          matched_local_order_count: 2,
          unmatched_local_orders: [{ order_execution_id: "local-1" }],
          unmatched_gateway_orders: [{ order_id: "gateway-1" }],
        }}
      />,
    );
    const panel = within(view.container);

    expect(panel.getByText("已匹配：2")).toBeInTheDocument();
    expect(panel.getByText("本地未匹配：1")).toBeInTheDocument();
    expect(panel.getByText("Binance 未匹配：1")).toBeInTheDocument();
  });

  it("shows an explicit unavailable state instead of implying REST polling", () => {
    render(
      <AppShell streamStatus="offline" error="服务暂时不可用，请稍后重试">
        <div>内容</div>
      </AppShell>,
    );

    expect(screen.getByText("服务不可用")).toBeInTheDocument();
  });

  it("labels Testnet acceptance fills as non-strategy audit records", () => {
    render(
      <OrdersTable
        orders={[
          {
            order_execution_id: "acceptance-1",
            created_at: "2026-07-12T00:43:14Z",
            symbol: "BTC/USDT",
            direction: "long",
            execution_status: "filled",
            entry_context: { execution_kind: "testnet_acceptance", order_type: "market" },
          },
        ]}
        onCancel={vi.fn()}
      />,
    );

    expect(screen.getByText("连通性验收（不计策略收益）")).toBeInTheDocument();
    expect(screen.getByText("已成交")).toBeInTheDocument();
    expect(screen.getByText("多")).toBeInTheDocument();
  });

  it("labels signal observation gateway fills as Binance sampling strategy orders", () => {
    render(
      <OrdersTable
        orders={[
          {
            order_execution_id: "sample-1",
            created_at: "2026-07-17T00:43:14Z",
            symbol: "ETH/USDT",
            direction: "short",
            execution_status: "filled",
            paper_run_id: "observation-run",
            gateway_order_id: "gateway-sample-1",
            entry_context: {
              execution_kind: "strategy_trade",
              strategy_lane: "signal_observation",
              order_type: "market",
            },
          },
        ]}
        onCancel={vi.fn()}
      />,
    );

    expect(screen.getByText("Binance 真实信号采样（不计策略收益）")).toBeInTheDocument();
  });

  it("translates stale market data status in the page header", () => {
    render(
      <AppShell overview={{ global_risk_status: "normal" }} snapshot={{ data_status: "stale" }} streamStatus="polling">
        <div>内容</div>
      </AppShell>,
    );

    expect(screen.getByText("数据延迟")).toBeInTheDocument();
  });

  it("keeps automatic settings controls controlled when the API returns null values", () => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});

    const view = render(
      <AutoSettingsPanel
        paperRunId="paper-1"
        autoSettings={{
          execution_mode: null,
          max_leverage: null,
          order_notional_usdt: null,
          stoploss: { atr_multiple: null },
        }}
      />,
    );

    const panel = within(view.container);
    expect(panel.getByLabelText("执行模式")).toHaveValue("binance_simulation_first");
    expect(panel.getByLabelText("杠杆")).toHaveValue(10);
    expect(panel.getByLabelText("ATR止损")).toHaveValue(2);
    expect(consoleError).not.toHaveBeenCalled();
    consoleError.mockRestore();
  });
});
