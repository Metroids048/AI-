import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RuntimeTruthPanel } from "./RuntimeTruthPanel";

describe("RuntimeTruthPanel", () => {
  it("shows the terminal no-trade reason and explicit unavailable exchange state", () => {
    render(
      <RuntimeTruthPanel
        symbol="BTC/USDT"
        runtime={{
          snapshot: {
            exchange: {
              value: null,
              source: "BINANCE_USDT_M_TESTNET",
              observed_at: "2026-07-27T06:00:00Z",
              freshness: "unavailable",
              status: "unavailable",
              error: "gateway timeout",
            },
            mismatch: {
              status: "unavailable",
              value: { consistent: false },
            },
            scheduler: {
              status: "unavailable",
              error: "scheduler_heartbeat_stale",
              source: "RUNTIME_SCHEDULER_HEARTBEAT",
              freshness: "stale",
            },
            data_freshness: {
              status: "available",
              value: {
                data_fresh: true,
                exchange_info_ready: true,
              },
              source: "RUNTIME_SCHEDULER_HEARTBEAT",
              freshness: "current",
            },
            strategy_evidence: {
              status: "available",
              value: {
                strategy_lane: "directional",
                acceptance_symbols: ["BTC/USDT", "ETH/USDT"],
                simulation_sampling_fallback_enabled: true,
                execution_mode: "binance_testnet",
                evidence_class: "NON_PROMOTABLE_PIPELINE_SAMPLE",
              },
              source: "PAPER_RUN_REPOSITORY",
              freshness: "current",
            },
          },
          decisions: [
            {
              symbol: "BTC/USDT",
              status: "SKIPPED",
              terminal_stage: "regime_confirmed",
              reason_code: "ONE_HOUR_REGIME_NOT_ALIGNED",
              bar_time: "2026-07-27T05:45:00Z",
            },
          ],
          llmInvocations: [
            {
              called: false,
              provider: null,
              model: null,
              skip_reason: "NO_DETERMINISTIC_CANDIDATE",
              total_tokens: 0,
            },
          ],
          streamStatus: "polling",
          lastSuccessAt: "2026-07-27T06:00:00Z",
          error: "",
        }}
      />,
    );

    expect(screen.getByText("为什么没有交易")).toBeTruthy();
    expect(screen.getByText("终止阶段：regime_confirmed")).toBeTruthy();
    expect(screen.getByText("原因：ONE_HOUR_REGIME_NOT_ALIGNED")).toBeTruthy();
    expect(screen.getByText(/交易所数据不可用：gateway timeout/)).toBeTruthy();
    expect(screen.getByText(/NO_DETERMINISTIC_CANDIDATE/)).toBeTruthy();
    expect(screen.getByText("Positions")).toBeTruthy();
    expect(screen.getByText("Exchange Orders")).toBeTruthy();
    expect(screen.getByText("Mismatch / Reconciliation")).toBeTruthy();
    expect(screen.getByText("Protections")).toBeTruthy();
    expect(screen.getByText("Data Freshness")).toBeTruthy();
    expect(screen.getByText(/数据新鲜/)).toBeTruthy();
    expect(screen.getByText("Strategy Evidence")).toBeTruthy();
    expect(screen.getByText("directional")).toBeTruthy();
    expect(screen.getByText(/NON_PROMOTABLE_PIPELINE_SAMPLE/)).toBeTruthy();
  });
});
