import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { RuntimeTruthPanel } from "./RuntimeTruthPanel";

afterEach(cleanup);

describe("RuntimeTruthPanel", () => {
  it("makes an active scheduler with no entry authority visibly blocked", () => {
    render(
      <RuntimeTruthPanel
        symbol="BTC/USDT"
        runtime={{
          snapshot: {
            exchange: { status: "available" },
            scheduler: { status: "available" },
            entry_runtime: {
              status: "available",
              value: {
                trading_state: "ENTRY_BLOCKED",
                entry_authority: "NONE",
                entry_authority_reason: "production_pending",
                production_authorization_state: "PENDING",
              },
            },
          },
          decisions: [],
          streamStatus: "live",
        }}
      />,
    );

    expect(screen.getByText("自动新开仓：权限阻塞")).toBeTruthy();
    expect(screen.getByText(/暂无通过验证的生产策略/)).toBeTruthy();
    expect(screen.getByText("自动平仓")).toBeTruthy();
  });

  it("identifies isolated Testnet Canary trading", () => {
    render(
      <RuntimeTruthPanel
        symbol="BTC/USDT"
        runtime={{
          snapshot: {
            exchange: { status: "available" },
            scheduler: { status: "available" },
            entry_runtime: {
              status: "available",
              value: {
                trading_state: "TRADING_READY",
                entry_authority: "TESTNET_CANARY",
                active_entry_strategy: "testnet_sampling_v2",
                production_authorization_state: "PENDING",
              },
            },
          },
          decisions: [],
          streamStatus: "live",
        }}
      />,
    );

    expect(screen.getByText("Testnet Canary 自动交易中")).toBeTruthy();
    expect(screen.getByText(/不进入 Production 晋升证据/)).toBeTruthy();
  });

  it("shows Testnet Forward readiness and active snapshot validity", () => {
    render(
      <RuntimeTruthPanel
        symbol="BTC/USDT"
        runtime={{
          snapshot: {
            exchange: { status: "available" },
            scheduler: { status: "available" },
            entry_runtime: {
              status: "available",
              value: {
                trading_state: "TRADING_READY",
                entry_authority: "TESTNET_FORWARD",
                active_entry_strategy: "validated-forward-v1",
                active_config_snapshot_id: "snapshot-1",
                active_snapshot_valid: true,
                forward_authorized: true,
              },
            },
          },
          decisions: [],
          streamStatus: "live",
        }}
      />,
    );

    expect(screen.getByText("Testnet Forward 自动交易就绪")).toBeTruthy();
    expect(screen.getByText("Active Snapshot：snapshot-1")).toBeTruthy();
    expect(screen.getByText("可自动交易")).toBeTruthy();
  });

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
              strategy: "testnet_sampling_v2",
              entry_authority: "TESTNET_CANARY",
              signal_generated: false,
              entry_gate_result: "regime_confirmed",
              entry_submitted: false,
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
    expect(screen.getByText("开仓权限：TESTNET_CANARY")).toBeTruthy();
    expect(screen.getByText("订单已提交：否")).toBeTruthy();
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

  it("renders normalized V2 decision facts without legacy funnel fields", () => {
    render(
      <RuntimeTruthPanel
        symbol="BTC/USDT"
        runtime={{
          snapshot: {
            exchange: { status: "available" },
            scheduler: { status: "available" },
          },
          decisions: [
            {
              symbol: "BTC/USDT",
              last_decision_at: "2026-08-12T15:13:31Z",
              strategy: "testnet_sampling_v2",
              entry_authority: "TESTNET_CANARY",
              signal_generated: false,
              entry_gate_result: "CANDLE_CLOSED",
              entry_submitted: false,
              terminal_reason: "DUPLICATE_DECISION",
            },
          ],
          streamStatus: "live",
        }}
      />,
    );

    expect(screen.getByText("终止阶段：CANDLE_CLOSED")).toBeTruthy();
    expect(screen.getByText("原因：DUPLICATE_DECISION")).toBeTruthy();
    expect(screen.getByText(/最后策略判断：/)).toBeTruthy();
  });
});
