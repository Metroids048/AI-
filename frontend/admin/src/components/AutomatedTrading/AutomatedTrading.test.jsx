/**
 * Tests for AutomatedTrading V2 components (Task 14).
 */
import { render, screen, cleanup } from "@testing-library/react";
import { describe, it, expect, afterEach, beforeEach } from "vitest";
import React from "react";

import { RuntimeOverview } from "./RuntimeOverview";
import { WhyNoTrade } from "./WhyNoTrade";
import { ExchangeVsLocal } from "./ExchangeVsLocal";

// Global cleanup after each test to prevent DOM pollution
afterEach(() => {
  cleanup();
});

// ---------------------------------------------------------------------------
// RuntimeOverview
// ---------------------------------------------------------------------------

const MOCK_SNAPSHOT = {
  engine: {
    engine_id: "automated-trading-v2",
    mode: "BINANCE_TESTNET",
    activation: "ACTIVE",
    entry_enabled: true,
    mainnet_supported: false,
  },
  scheduler: { running: true },
  exchange: { available: true, positions: [], freshness: "FRESH" },
  reconciliation: { status: "HEALTHY", mismatches: [] },
};

describe("RuntimeOverview", () => {
  it("shows loading state when no snapshot", () => {
    render(<RuntimeOverview snapshot={null} error={null} lastSuccessAt={null} />);
    expect(screen.getByText(/加载中/)).toBeTruthy();
  });

  it("shows error state with no snapshot and error present", () => {
    render(
      <RuntimeOverview
        snapshot={null}
        error="API unavailable"
        lastSuccessAt={null}
      />
    );
    expect(screen.getByText(/未接通/)).toBeTruthy();
  });

  it("renders engine mode from snapshot", () => {
    render(<RuntimeOverview snapshot={MOCK_SNAPSHOT} error={null} lastSuccessAt={null} />);
    expect(screen.getByText("BINANCE_TESTNET")).toBeTruthy();
  });

  it("renders activation from snapshot", () => {
    render(<RuntimeOverview snapshot={MOCK_SNAPSHOT} error={null} lastSuccessAt={null} />);
    expect(screen.getByText("ACTIVE")).toBeTruthy();
  });

  it("shows 不支持 for mainnet", () => {
    render(<RuntimeOverview snapshot={MOCK_SNAPSHOT} error={null} lastSuccessAt={null} />);
    expect(screen.getByText(/不支持/)).toBeTruthy();
  });

  it("shows reconciliation status", () => {
    render(<RuntimeOverview snapshot={MOCK_SNAPSHOT} error={null} lastSuccessAt={null} />);
    expect(screen.getByText("HEALTHY")).toBeTruthy();
  });

  it("shows entry disabled when entry_enabled is false", () => {
    const snap = {
      ...MOCK_SNAPSHOT,
      engine: { ...MOCK_SNAPSHOT.engine, entry_enabled: false },
    };
    render(<RuntimeOverview snapshot={snap} error={null} lastSuccessAt={null} />);
    expect(screen.getByText(/已禁止/)).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// WhyNoTrade
// ---------------------------------------------------------------------------

describe("WhyNoTrade", () => {
  it("shows empty message when no decisions", () => {
    render(<WhyNoTrade decisions={[]} />);
    expect(screen.getByText(/暂无决策记录/)).toBeTruthy();
  });

  it("renders the reason in Chinese and keeps the raw code recoverable", () => {
    const decisions = [
      {
        symbol: "BTC/USDT",
        terminal_stage: "entry_signal",
        reason_code: "NO_ENTRY_SIGNAL",
        evaluated_at: new Date().toISOString(),
        candidate_id: null,
        exchange_submitted: false,
      },
    ];
    render(<WhyNoTrade decisions={decisions} />);
    const reason = screen.getByText("无入场信号");
    expect(reason).toBeTruthy();
    // The machine-readable code must stay available for debugging.
    expect(reason.getAttribute("title")).toBe("NO_ENTRY_SIGNAL");
    expect(screen.getByText("入场信号")).toBeTruthy();
    expect(screen.queryByText("NO_ENTRY_SIGNAL")).toBeNull();
    expect(screen.getByText("BTC/USDT")).toBeTruthy();
  });

  it("strips the perpetual suffix from the symbol", () => {
    const decisions = [
      {
        symbol: "BTC/USDT:USDT",
        terminal_stage: "entry_signal",
        reason_code: "ENSEMBLE_DISCARDED",
        evaluated_at: new Date().toISOString(),
        candidate_id: null,
        exchange_submitted: false,
      },
    ];
    render(<WhyNoTrade decisions={decisions} />);
    expect(screen.getByText("BTC/USDT")).toBeTruthy();
    expect(screen.getByText("多策略投票未形成方向")).toBeTruthy();
  });

  it("shows exchange submitted badge when true", () => {
    const decisions = [
      {
        symbol: "ETH/USDT",
        terminal_stage: "PROTECTION_CONFIRMED",
        reason_code: "OK",
        evaluated_at: new Date().toISOString(),
        candidate_id: "c-1",
        exchange_submitted: true,
      },
    ];
    render(<WhyNoTrade decisions={decisions} />);
    expect(screen.getByText(/已提交至交易所/)).toBeTruthy();
  });

  it("shows latest decision per symbol when duplicates", () => {
    const earlier = new Date(Date.now() - 60_000).toISOString();
    const later = new Date().toISOString();
    const decisions = [
      {
        symbol: "BTC/USDT",
        terminal_stage: "ENTRY_SIGNAL_EVALUATED",
        reason_code: "NO_ENTRY_SIGNAL",
        evaluated_at: earlier,
        candidate_id: null,
        exchange_submitted: false,
      },
      {
        symbol: "BTC/USDT",
        terminal_stage: "CANDIDATE_CREATED",
        reason_code: "CANDIDATE_READY",
        evaluated_at: later,
        candidate_id: "c-2",
        exchange_submitted: false,
      },
    ];
    render(<WhyNoTrade decisions={decisions} />);
    // Latest should win: CANDIDATE_READY, not NO_ENTRY_SIGNAL
    expect(screen.getByText("CANDIDATE_READY")).toBeTruthy();
    expect(screen.queryByText("NO_ENTRY_SIGNAL")).toBeNull();
  });

  it("renders normalized V2 Runtime Truth decision fields", () => {
    const decisions = [
      {
        symbol: "BTC/USDT",
        last_decision_at: new Date().toISOString(),
        entry_gate_result: "CANDLE_CLOSED",
        terminal_reason: "DUPLICATE_DECISION",
        entry_submitted: false,
      },
    ];
    render(<WhyNoTrade decisions={decisions} />);
    expect(screen.getByText("CANDLE_CLOSED")).toBeTruthy();
    const reason = screen.getByText("重复决策");
    expect(reason.getAttribute("title")).toBe("DUPLICATE_DECISION");
  });
});

// ---------------------------------------------------------------------------
// ExchangeVsLocal
// ---------------------------------------------------------------------------

describe("ExchangeVsLocal", () => {
  it("shows loading when no data", () => {
    render(<ExchangeVsLocal positionsData={null} />);
    expect(screen.getByText(/加载中/)).toBeTruthy();
  });

  it("renders exchange column header", () => {
    const data = {
      exchange: { source: "BINANCE_TESTNET", available: true, positions: [] },
      local_projection: { source: "V2_LOCAL_PROJECTION", positions: [] },
    };
    render(<ExchangeVsLocal positionsData={data} />);
    expect(screen.getByText("Binance Testnet (真相)")).toBeTruthy();
    expect(screen.getByText("本地投影 (V2)")).toBeTruthy();
  });

  it("shows 未接通 when exchange unavailable", () => {
    const data = {
      exchange: { source: "BINANCE_TESTNET", available: false, positions: [] },
      local_projection: { source: "V2_LOCAL_PROJECTION", positions: [] },
    };
    render(<ExchangeVsLocal positionsData={data} />);
    expect(screen.getByText("未接通")).toBeTruthy();
  });

  it("shows mismatch warning when counts differ", () => {
    const data = {
      exchange: {
        source: "BINANCE_TESTNET",
        available: true,
        positions: [{ symbol: "BTC/USDT", qty: "0.001" }],
      },
      local_projection: { source: "V2_LOCAL_PROJECTION", positions: [] },
    };
    render(<ExchangeVsLocal positionsData={data} />);
    expect(screen.getByText(/不一致/)).toBeTruthy();
  });

  it("shows no mismatch when counts match", () => {
    const data = {
      exchange: { source: "BINANCE_TESTNET", available: true, positions: [] },
      local_projection: { source: "V2_LOCAL_PROJECTION", positions: [] },
    };
    render(<ExchangeVsLocal positionsData={data} />);
    expect(screen.queryByText(/不一致/)).toBeNull();
  });
});
