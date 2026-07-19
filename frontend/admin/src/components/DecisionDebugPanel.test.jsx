import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { buildTop20EvidenceRows, DecisionDebugPanel, RejectionFunnelPanel } from "./RuntimePanels";

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

describe("Top20 evidence", () => {
  it("distinguishes candidate, scanned, signal, gate, submitted, and filled states", () => {
    const rows = buildTop20EvidenceRows({
      candidate_symbols: ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "BNB/USDT", "SUI/USDT"],
      last_scanned_symbols: ["ETH/USDT", "SOL/USDT", "XRP/USDT", "BNB/USDT", "SUI/USDT"],
      last_cycle_decisions: [
        { symbol: "SOL/USDT", action: "skip", decision_trace: { signals: [{ source: "technical_rsi" }] } },
        { symbol: "XRP/USDT", action: "skip", decision_trace: { signals: [{}], pipeline_status: "bet_taken" } },
        { symbol: "BNB/USDT", action: "queued", order_execution_id: "order-1", decision_trace: { signals: [{}], pipeline_status: "bet_taken" } },
        { symbol: "SUI/USDT", action: "open_long", order_execution_id: "order-2", decision_trace: { signals: [{}], pipeline_status: "bet_taken" } },
      ],
    });

    expect(rows.map((item) => item.label)).toEqual(["候选", "已采集", "有信号", "过 Gate", "已提交", "已成交"]);
  });
});

describe("RejectionFunnelPanel", () => {
  it("keeps gateway failures visible with their latest safe error", () => {
    render(
      <RejectionFunnelPanel
        summary={{
          counts: { "网关失败": 2, "OOS 证据": 1 },
          recent: [{ order_execution_id: "order-1", symbol: "BTC/USDT", category: "网关失败", message: "-2022" }],
        }}
      />,
    );

    expect(screen.getByText("拒单漏斗")).toBeInTheDocument();
    expect(screen.getByText("网关失败: 2")).toBeInTheDocument();
    expect(screen.getByText("BTC/USDT · 网关失败 · -2022")).toBeInTheDocument();
  });
});
