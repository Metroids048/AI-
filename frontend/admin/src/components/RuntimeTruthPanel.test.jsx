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
  });
});
