import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { TradingSummaryHero } from "./TradingSummaryHero";

describe("TradingSummaryHero", () => {
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

  it("displays 未接通 when account is null or not connected", () => {
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

    const disconnectedElements = screen.getAllByText("未接通");
    expect(disconnectedElements.length).toBeGreaterThanOrEqual(2);
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

  it("displays 暂无数据 when PnL is unknown", () => {
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

    expect(screen.getByText("暂无数据")).toBeTruthy();
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
  });

  it("shows Binance testnet link when URL is available", () => {
    render(
      <TradingSummaryHero
        account={{ connected: true, testnet_url: "https://testnet.binance.vision" }}
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
    expect(link.getAttribute("href")).toBe("https://testnet.binance.vision");
    expect(link.getAttribute("target")).toBe("_blank");
  });

  it("does not show Binance link when URL is not available", () => {
    render(
      <TradingSummaryHero
        account={{ connected: true, wallet_balance: 10000 }}
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

    expect(screen.queryByRole("link", { name: /打开币安模拟盘/ })).toBeNull();
  });
});
