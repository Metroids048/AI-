import { afterEach, describe, it, expect } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { TradingSummaryHero } from "./TradingSummaryHero";

afterEach(() => {
  cleanup();
});

describe("TradingSummaryHero", () => {
  it("shows the deterministic no-trade monitoring summary on the home hero", () => {
    render(
      <TradingSummaryHero
        account={{ connected: true, wallet_balance: 10000 }}
        positions={[]}
        orders={[]}
        decisions={[]}
        noTradeSummary={{
          summary_code: "HEALTHY_WAITING_FOR_SIGNAL",
          summary_category: "STRATEGY_NO_SIGNAL",
          hours_since_last_entry: 3.25,
          decisions: { effective: 5, duplicate: 7, reason_counts: { NO_ENTRY_SIGNAL: 5 } },
        }}
        tradingStatus={{ is_active: true }}
        globalRiskStatus={{ entry_allowed: true }}
        streamStatus="live"
        selectedSymbol="BTC/USDT"
        lastSuccessAt={null}
      />,
    );
    expect(screen.getByText("不开单监控")).toBeTruthy();
    expect(screen.getByText("已 3.3 小时未产生新开仓")).toBeTruthy();
    expect(screen.getByText("策略正在等待交易机会")).toBeTruthy();
    expect(screen.getByText("INFRASTRUCTURE: PASS / WAITING_FOR_SIGNAL")).toBeTruthy();
  });

  it("renders main heading in Chinese", () => {
    render(
      <TradingSummaryHero
        account={null}
        positions={[]}
        orders={[]}
        decisions={[]}
        tradingStatus={null}
        globalRiskStatus={null}
        streamStatus="connecting"
        selectedSymbol="BTC/USDT"
        lastSuccessAt={null}
      />
    );

    expect(screen.getByText("AI 量化自动交易总览")).toBeTruthy();
    expect(screen.getByText(/策略扫描、币安模拟盘账户/)).toBeTruthy();
  });

  it("displays account balance when connected", () => {
    render(
      <TradingSummaryHero
        account={{ wallet_balance: 10000.5, available_balance: 8500.25, connected: true }}
        positions={[]}
        orders={[]}
        decisions={[]}
        tradingStatus={null}
        globalRiskStatus={null}
        streamStatus="live"
        selectedSymbol="BTC/USDT"
        lastSuccessAt={null}
      />
    );

    expect(screen.getByText("10000.50 USDT")).toBeTruthy();
    expect(screen.getByText("8500.25 USDT")).toBeTruthy();
  });

  it("displays 暂不可用 when account is null or not connected", () => {
    render(
      <TradingSummaryHero
        account={null}
        positions={[]}
        orders={[]}
        decisions={[]}
        tradingStatus={null}
        globalRiskStatus={null}
        streamStatus="offline"
        selectedSymbol="BTC/USDT"
        lastSuccessAt={null}
      />
    );

    const disconnectedElements = screen.getAllByText("暂不可用");
    expect(disconnectedElements.length).toBeGreaterThanOrEqual(2);
  });

  it("does not invent zero values or normal risk while runtime truth is unavailable", () => {
    render(
      <TradingSummaryHero
        account={{ connected: false, status: "unavailable", error: "gateway timeout" }}
        positions={null}
        orders={null}
        decisions={[]}
        tradingStatus={null}
        globalRiskStatus={null}
        streamStatus="offline"
        selectedSymbol="BTC/USDT"
        lastSuccessAt={null}
      />
    );

    expect(screen.getAllByText("暂不可用").length).toBeGreaterThanOrEqual(4);
    expect(screen.getByText("状态待确认")).toBeTruthy();
    expect(screen.queryByText("正常")).toBeNull();
    expect(screen.queryByText("+0.00 USDT")).toBeNull();
  });

  it("calculates and displays unrealized PnL from positions", () => {
    const positions = [
      { unrealized_pnl: 150.5 },
      { unrealized_pnl: -50.25 },
    ];

    render(
      <TradingSummaryHero
        account={{ connected: true }}
        positions={positions}
        orders={[]}
        decisions={[]}
        tradingStatus={null}
        globalRiskStatus={null}
        streamStatus="live"
        selectedSymbol="BTC/USDT"
        lastSuccessAt={null}
      />
    );

    expect(screen.getByText("+100.25 USDT")).toBeTruthy();
  });

  it("displays 暂不可用 when PnL is unknown", () => {
    render(
      <TradingSummaryHero
        account={{ connected: true }}
        positions={null}
        orders={[]}
        decisions={[]}
        tradingStatus={null}
        globalRiskStatus={null}
        streamStatus="live"
        selectedSymbol="BTC/USDT"
        lastSuccessAt={null}
      />
    );

    expect(screen.getAllByText("暂不可用").length).toBeGreaterThan(0);
  });

  it("displays strategy status in Chinese", () => {
    const { rerender } = render(
      <TradingSummaryHero
        account={null}
        positions={[]}
        orders={[]}
        decisions={[]}
        tradingStatus={{ is_active: true }}
        globalRiskStatus={null}
        streamStatus="live"
        selectedSymbol="BTC/USDT"
        lastSuccessAt={null}
      />
    );

    expect(screen.getByText("运行中")).toBeTruthy();

    rerender(
      <TradingSummaryHero
        account={null}
        positions={[]}
        orders={[]}
        decisions={[]}
        tradingStatus={{ is_active: false }}
        globalRiskStatus={null}
        streamStatus="live"
        selectedSymbol="BTC/USDT"
        lastSuccessAt={null}
      />
    );

    expect(screen.getByText("已暂停")).toBeTruthy();

    rerender(
      <TradingSummaryHero
        account={null}
        positions={[]}
        orders={[]}
        decisions={[]}
        tradingStatus={null}
        globalRiskStatus={null}
        streamStatus="live"
        selectedSymbol="BTC/USDT"
        lastSuccessAt={null}
      />
    );

    expect(screen.getByText("状态未知")).toBeTruthy();
  });

  it("does not display raw developer terms", () => {
    render(
      <TradingSummaryHero
        account={{ connected: true, wallet_balance: 10000 }}
        positions={[]}
        orders={[]}
        decisions={[]}
        tradingStatus={{ is_active: true }}
        globalRiskStatus={{ entry_allowed: true }}
        streamStatus="live"
        selectedSymbol="BTC/USDT"
        lastSuccessAt={null}
      />
    );

    // 禁止出现的开发者术语
    expect(screen.queryByText(/V2/i)).toBeNull();
    expect(screen.queryByText(/engine/i)).toBeNull();
    expect(screen.queryByText(/Mode/)).toBeNull();
    expect(screen.queryByText(/Activation/)).toBeNull();
    expect(screen.queryByText(/Scheduler/)).toBeNull();
    expect(screen.queryByText(/Mainnet/)).toBeNull();
    expect(screen.queryByText(/Runtime Truth/)).toBeNull();
    expect(screen.queryByText(/Mock/)).toBeNull();
    expect(screen.queryByText(/Paper Cycle/)).toBeNull();
  });

  it("displays latest decision in Chinese", () => {
    const decisions = [
      {
        symbol: "BTC/USDT",
        terminal_reason: "technical_signals_insufficient",
        created_at: "2026-08-01T10:00:00Z",
      },
    ];

    render(
      <TradingSummaryHero
        account={null}
        positions={[]}
        orders={[]}
        decisions={decisions}
        tradingStatus={null}
        globalRiskStatus={null}
        streamStatus="live"
        selectedSymbol="BTC/USDT"
        lastSuccessAt={null}
      />
    );

    const headingElements = screen.getAllByText(/最新决策/);
    expect(headingElements.length).toBeGreaterThan(0);
    expect(screen.getByText(/BTC\/USDT/)).toBeTruthy();
    expect(screen.getByText("技术信号不足")).toBeTruthy();
    expect(screen.queryByText("technical_signals_insufficient")).toBeNull();
  });

  it("always shows Binance testnet link using web_ui_url", () => {
    render(
      <TradingSummaryHero
        account={{ connected: true, web_ui_url: "https://testnet.binancefuture.com/en/futures/ETHUSDT" }}
        positions={[]}
        orders={[]}
        decisions={[]}
        tradingStatus={null}
        globalRiskStatus={null}
        streamStatus="live"
        selectedSymbol="BTC/USDT"
        lastSuccessAt={null}
      />
    );

    const link = screen.getByRole("link", { name: /打开币安模拟盘/ });
    expect(link).toBeTruthy();
    expect(link.getAttribute("href")).toBe("https://testnet.binancefuture.com/en/futures/ETHUSDT");
    expect(link.getAttribute("target")).toBe("_blank");
  });

  it("keeps default Binance testnet link when account is disconnected", () => {
    render(
      <TradingSummaryHero
        account={{ connected: false, error: "binance credentials not configured" }}
        positions={[]}
        orders={[]}
        decisions={[]}
        tradingStatus={null}
        globalRiskStatus={null}
        streamStatus="live"
        selectedSymbol="BTC/USDT"
        lastSuccessAt={null}
      />
    );

    const link = screen.getByRole("link", { name: /打开币安模拟盘/ });
    expect(link.getAttribute("href")).toBe("https://testnet.binancefuture.com/en/futures/BTCUSDT");
    expect(screen.getByText(/账户未接通：binance credentials not configured/)).toBeTruthy();
  });

  it("keeps default Binance testnet link when account is null", () => {
    render(
      <TradingSummaryHero
        account={null}
        positions={[]}
        orders={[]}
        decisions={[]}
        tradingStatus={null}
        globalRiskStatus={null}
        streamStatus="offline"
        selectedSymbol="BTC/USDT"
        lastSuccessAt={null}
      />
    );

    const link = screen.getByRole("link", { name: /打开币安模拟盘/ });
    expect(link.getAttribute("href")).toBe("https://testnet.binancefuture.com/en/futures/BTCUSDT");
  });
});
