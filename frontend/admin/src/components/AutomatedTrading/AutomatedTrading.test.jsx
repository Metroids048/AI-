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
  it("shows scheduler failure instead of implying the strategy is waiting", () => {
    render(
      <WhyNoTrade
        decisions={[]}
        noTradeSummary={{
          summary_code: "SCHEDULER_OFFLINE",
          runtime_status: "异常",
          window_hours: 3,
          hours_since_last_entry: null,
          scheduler: { running: false },
        }}
      />,
    );
    expect(screen.getByText("自动交易程序异常：调度器心跳中断")).toBeTruthy();
    expect(screen.queryByText("策略正在等待交易机会")).toBeNull();
  });

  it("uses the healthy time-window summary and excludes duplicate decisions from effective counts", () => {
    render(
      <WhyNoTrade
        decisions={[]}
        noTradeSummary={{
          summary_code: "HEALTHY_WAITING_FOR_SIGNAL",
          summary_category: "STRATEGY_NO_SIGNAL",
          runtime_status: "正常",
          window_hours: 3,
          hours_since_last_entry: 3.25,
          decisions: { total: 12, effective: 5, duplicate: 7, reason_counts: { NO_ENTRY_SIGNAL: 5 } },
        }}
      />,
    );
    expect(screen.getByText("已 3.3 小时未产生新开仓")).toBeTruthy();
    expect(screen.getByText("有效策略判断 5 次")).toBeTruthy();
    expect(screen.getByText("重复轮询 7 次")).toBeTruthy();
    expect(screen.getByText("策略正在等待交易机会")).toBeTruthy();
    expect(screen.getByText("INFRASTRUCTURE: PASS / WAITING_FOR_SIGNAL")).toBeTruthy();
    expect(screen.queryByText("DUPLICATE_DECISION")).toBeNull();
  });

  it("does not describe an authorization block as healthy waiting", () => {
    render(
      <WhyNoTrade
        decisions={[]}
        noTradeSummary={{
          summary_code: "ENTRY_PAUSED",
          summary_category: "AUTHORIZATION_BLOCKED",
          runtime_status: "正常",
          current_status: { runtime_health: "healthy", active_blocker: "NO_FORWARD_VALIDATION_CANDIDATE" },
          entry_runtime: { entry_authority: "NONE", entry_authorized: false },
        }}
      />,
    );

    expect(screen.getByText("开仓授权阻塞")).toBeTruthy();
    expect(screen.queryByText("正常等待交易机会")).toBeNull();
  });

  it("shows the deterministic entry-block reason in Chinese", () => {
    render(
      <WhyNoTrade
        decisions={[]}
        noTradeSummary={{
          summary_code: "ENTRY_BLOCKED",
          runtime_status: "正常",
          window_hours: 3,
          hours_since_last_entry: 4,
          decisions: { total: 4, effective: 4, duplicate: 0, reason_counts: { PRICE_DRIFT_EXCEEDED: 4 }, dominant_reason: "PRICE_DRIFT_EXCEEDED" },
        }}
      />,
    );
    expect(screen.getByText("新开仓被拦截：价格漂移超限（4 次）")).toBeTruthy();
  });

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

  it("explains the current Canary position capacity and blocker share", () => {
    render(
      <WhyNoTrade
        decisions={[]}
        noTradeSummary={{
          summary_code: "ENTRY_BLOCKED",
          hours_since_last_entry: 1,
          decisions: { effective: 4, duplicate: 0, reason_counts: { MAX_OPEN_EXPOSURES: 3, MACD_DIRECTION_MISMATCH: 1 } },
          throughput: {
            current_open_positions: 1,
            effective_max_open_positions: 1,
            remaining_slots: 0,
            at_capacity: true,
            blocker_counts: { MAX_OPEN_EXPOSURES: 3, MACD_DIRECTION_MISMATCH: 1 },
            blocker_total: 4,
          },
        }}
      />,
    );
    expect(screen.getByText(/当前已达到 Testnet Canary 持仓上限：1 \/ 1/)).toBeTruthy();
    expect(screen.getByText(/已达到持仓上限 3 次（75.0%）/)).toBeTruthy();
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
