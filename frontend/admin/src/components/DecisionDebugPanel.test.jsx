import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DecisionDebugPanel } from "./RuntimePanels";

describe("DecisionDebugPanel", () => {
  it("renders ensemble, meta-label, and veto evidence", () => {
    render(
      <DecisionDebugPanel
        decisionTrace={{
          last_cycle_decisions: [
            {
              symbol: "BTC/USDT",
              action: "open_long",
              idempotency_key: "paper-1:BTC/USDT:1h:2026-07-06T00:00:00Z",
              decision_trace: {
                pipeline_status: "bet_taken",
                signals: [{ source: "price_action_donchian", reason: "donchian_resistance_breakout", side: "long" }],
                ensemble: { fused_direction: "long", fused_confidence: 0.83 },
                meta_label: { bet_decision: "bet_taken", position_size_fraction: 0.6 },
                veto_result: { veto: false, veto_reason: "no blocking risk evidence" },
              },
            },
          ],
        }}
      />,
    );

    expect(screen.getByText("决策链路")).toBeInTheDocument();
    expect(screen.getByText("BTC/USDT")).toBeInTheDocument();
    expect(screen.getByText(/融合：long/)).toBeInTheDocument();
    expect(screen.getByText(/Meta：bet_taken/)).toBeInTheDocument();
    expect(screen.getByText("no blocking risk evidence")).toBeInTheDocument();
  });
});
