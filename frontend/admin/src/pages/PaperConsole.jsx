import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { request } from "../api/client";
import { WhyNoTrade } from "../components/AutomatedTrading";
import { RuntimeTruthPanel } from "../components/RuntimeTruthPanel";
import { TradingSummaryHero } from "../components/TradingSummaryHero";
import { AppShell, DataState } from "../components/Common";
import { KlinePanel, MarketHeader } from "../components/MarketPanels";
import { ExecutionAcceptancePanel, TradingRecordsWorkspace } from "../components/TradingRecordsWorkspace";
import {
  AutoSettingsPanel,
  DecisionDebugPanel,
  DataSourcesPanel,
  MarketIntelligencePanel,
  MessageSourcesPanel,
  OrderSyncPanel,
  RejectionFunnelPanel,
  Top20MonitorPanel,
} from "../components/RuntimePanels";
import {
  ExchangePositionHint,
  FundingPanel,
  MarketList,
  OrdersTable,
  PositionsTable,
  TestnetAccountPanel,
  TradingTicket,
} from "../components/TradingConsolePanels";
import { useConsoleData } from "../hooks/useConsoleData";
import { useRuntimeTruth } from "../hooks/useRuntimeTruth";

const DEFAULT_SYMBOL = "BTC/USDT";
const DEFAULT_PERP = "BTC/USDT:USDT";
const DEFAULT_TIMEFRAME = "1m";
const DEFAULT_CANARY_SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "BNB/USDT"];

function asArray(value) {
  return Array.isArray(value) ? value : [];
}

function platformSymbol(symbol) {
  return String(symbol || "").replace(":USDT", "");
}

/** Map Binance Demo positions into the desk PositionsTable shape. */
export function deskPositionsFromAccount(account) {
  if (!account?.connected) return null;
  const syncedAt = account.synced_at ?? new Date().toISOString();
  return asArray(account.positions)
    .filter((position) => Math.abs(Number(position.quantity) || 0) > 0)
    .map((position) => ({
      position_snapshot_id: `binance:${position.symbol}:${position.side}`,
      symbol: platformSymbol(position.symbol),
      side: position.side,
      quantity: position.quantity,
      entry_price: position.entry_price,
      mark_price: position.mark_price,
      notional_usdt: position.notional_usdt,
      margin_usdt: position.margin_usdt,
      leverage: position.leverage,
      unrealized_pnl: position.unrealized_pnl,
      snapshot_time: position.open_time ?? position.created_at ?? position.update_time ?? syncedAt,
      source: "binance_exchange",
    }));
}

/** Prefer open exchange orders, then recent fills, for the desk Orders tab. */
export function deskOrdersFromAccount(account) {
  if (!account?.connected) return null;
  const openRows = asArray(account.open_orders).map((order) => ({
    order_execution_id: `binance-open:${order.order_id}`,
    symbol: platformSymbol(order.symbol),
    direction: String(order.side || "").toLowerCase() === "sell" ? "short" : "long",
    execution_status: String(order.status || "open").toLowerCase(),
    gateway_order_id: order.order_id,
    gateway_name: "binance_usdt_perpetual",
    entry_context: {
      execution_kind: "binance_open_order",
      order_type: order.order_type,
      quantity: order.quantity,
      actual_avg_price: order.avg_price,
      reduce_only: Boolean(order.reduce_only),
    },
    created_at: order.updated_at ?? account.synced_at,
  }));
  const recentRows = asArray(account.recent_orders).map((order) => ({
    order_execution_id: `binance:${order.order_id}`,
    symbol: platformSymbol(order.symbol),
    direction: String(order.side || "").toLowerCase() === "sell" ? "short" : "long",
    execution_status: String(order.status || "").toLowerCase() === "filled" ? "filled" : String(order.status || "submitted").toLowerCase(),
    gateway_order_id: order.order_id,
    gateway_name: "binance_usdt_perpetual",
    entry_context: {
      execution_kind: "binance_demo_reconciliation",
      order_type: order.order_type,
      quantity: order.quantity,
      actual_avg_price: order.avg_price,
      reduce_only: Boolean(order.reduce_only),
      strategy_performance_eligible: false,
    },
    created_at: order.updated_at ?? account.synced_at,
  }));
  const seen = new Set(openRows.map((row) => row.gateway_order_id));
  return [...openRows, ...recentRows.filter((row) => !seen.has(row.gateway_order_id))];
}

/** Prefer the persisted V2 exchange snapshot; never fall back to legacy Paper rows. */
export function deskPositionsFromRuntimeTruth(positionsData, account = null) {
  void account;
  const exchange = positionsData?.exchange;
  const exchangeValue = exchange?.status === "available"
    ? exchange.value
    : exchange?.available === true
      ? exchange
      : null;
  if (exchangeValue) {
    const observedAt = exchange.observed_at ?? new Date().toISOString();
    const ownershipRows = asArray(
      positionsData?.reconciliation?.value?.mismatch?.value?.ownership_positions
        ?? positionsData?.reconciliation?.value?.ownership?.system_v2_positions,
    );
    const ownershipByKey = new Map(
      ownershipRows.map((item) => [`${platformSymbol(item.symbol)}:${String(item.side ?? item.direction ?? "").toLowerCase()}`, item]),
    );
    return asArray(exchangeValue.positions)
      .map((position) => ({ ...position, quantity: position.quantity ?? position.contracts ?? position.positionAmt, direction: position.direction ?? position.side }))
      .filter((position) => Math.abs(Number(position.quantity) || 0) > 0)
      .map((position) => {
        const quantity = position.quantity;
        const markPrice = Number(position.mark_price || position.entry_price || 0);
        const notional = Math.abs(Number(quantity) || 0) * markPrice;
        const leverage = position.leverage ?? null;
        const ownership = ownershipByKey.get(
          `${platformSymbol(position.symbol)}:${String(position.direction ?? position.side ?? "").toLowerCase()}`,
        );
        const ownershipName = ownership?.ownership ?? position.ownership ?? "UNKNOWN";
        const externalManualQuantity = ownership?.external_manual_quantity ?? position.external_manual_quantity ?? 0;
        const managedQuantity = ownership?.managed_quantity ?? position.managed_quantity ?? quantity;
        return {
          position_snapshot_id: `v2-binance:${position.symbol}:${position.direction}`,
          symbol: platformSymbol(position.symbol),
          side: position.direction,
          quantity,
          entry_price: position.entry_price,
          mark_price: position.mark_price,
          notional_usdt: notional > 0 ? notional : null,
          margin_usdt: notional > 0 && leverage ? notional / leverage : null,
          leverage,
          unrealized_pnl: position.unrealized_pnl,
          snapshot_time: observedAt,
          ownership: ownershipName,
          external_manual_quantity: externalManualQuantity,
          managed_quantity: managedQuantity,
          strategy_performance_eligible: ownershipName === "SYSTEM_V2",
          source: ownershipName === "EXTERNAL_MANUAL" ? "binance_external_manual" : "binance_v2_reconciliation",
        };
      });
  }
  return null;
}

/** Prefer V2 reconciliation open orders, including an authoritative empty book. */
export function deskOrdersFromRuntimeTruth(runtimeSnapshot, account = null) {
  void account;
  const exchange = runtimeSnapshot?.exchange;
  const exchangeValue = exchange?.status === "available"
    ? exchange.value
    : exchange?.available === true
      ? exchange
      : null;
  if (exchangeValue) {
    const observedAt = exchange.observed_at ?? exchange.timestamp ?? new Date().toISOString();
    return asArray(exchangeValue.open_orders).map((order) => {
      const exchangeOrderId = order.exchange_order_id ?? order.id ?? order.orderId;
      return {
      order_execution_id: `v2-binance-open:${exchangeOrderId}`,
      symbol: platformSymbol(order.symbol),
      direction: String(order.side || "").toLowerCase() === "sell" ? "short" : "long",
      execution_status: String(order.status || "open").toLowerCase(),
      gateway_order_id: exchangeOrderId,
      gateway_name: "binance_usdt_perpetual",
      entry_context: {
        execution_kind: "v2_binance_open_order",
        order_type: order.order_type,
        quantity: order.quantity ?? order.amount,
        actual_avg_price: order.price ?? order.average,
        reduce_only: Boolean(order.reduce_only ?? order.reduceOnly),
      },
      created_at: observedAt,
    }; });
  }
  return null;
}

export function accountFromRuntimeSnapshot(snapshot) {
  const exchange = snapshot?.exchange;
  const account = exchange?.value?.account;
  if (exchange?.status === "available" && account) {
    return {
      ...account,
      connected: true,
      status: "available",
      synced_at: exchange.observed_at,
      web_ui_url: "https://testnet.binancefuture.com/en/futures/BTCUSDT",
    };
  }
  return {
    connected: false,
    status: exchange?.status ?? "unavailable",
    error: exchange?.error ?? "账户数据暂不可用",
    wallet_balance: null,
    available_balance: null,
    margin_balance: null,
    unrealized_pnl: null,
    open_position_count: null,
    web_ui_url: "https://testnet.binancefuture.com/en/futures/BTCUSDT",
  };
}

function tradingStatusFromRuntimeSnapshot(snapshot) {
  const scheduler = snapshot?.scheduler;
  return scheduler?.status === "available" && scheduler.value
    ? { is_active: scheduler.value.running === true }
    : null;
}

function riskStatusFromRuntime(runtime, noTradeSummary) {
  const reconciliation = runtime?.status && runtime.status !== "unavailable"
    ? runtime
    : noTradeSummary?.reconciliation;
  if (!reconciliation || reconciliation.status === "unavailable") return { status: "unknown", entry_allowed: null };
  if (reconciliation.entry_blocked_symbols?.length || reconciliation.blocked_symbols?.length) {
    return { status: "blocked", entry_allowed: false };
  }
  if (reconciliation.status) return { status: reconciliation.status, entry_allowed: true };
  return { status: "unknown", entry_allowed: null };
}

export function PaperConsole() {
  const [searchParams, setSearchParams] = useSearchParams();
  const symbol = searchParams.get("symbol") || DEFAULT_SYMBOL;
  const perpSymbol = searchParams.get("perp_symbol") || DEFAULT_PERP;
  const timeframe = searchParams.get("timeframe") || DEFAULT_TIMEFRAME;
  const [actionMessage, setActionMessage] = useState("");
  const data = useConsoleData(symbol, perpSymbol, timeframe);
  const runtime = useRuntimeTruth();
  const account = useMemo(() => accountFromRuntimeSnapshot(runtime.snapshot), [runtime.snapshot]);
  const manualMode = "paper";
  const canarySymbols = useMemo(
    () => runtime.snapshot?.scheduler?.value?.execution_symbols?.length
      ? runtime.snapshot.scheduler.value.execution_symbols.map(platformSymbol)
      : DEFAULT_CANARY_SYMBOLS,
    [runtime.snapshot],
  );
  const latestPaperRun = useMemo(
    () => (Array.isArray(data.overview?.paper_runs) ? data.overview.paper_runs.at(-1) : null),
    [data.overview?.paper_runs],
  );
  const autoPaperRunId = data.decisionTrace?.paper_run_id ?? latestPaperRun?.paper_run_id;
  const autoSettings = data.decisionTrace?.auto_settings ?? latestPaperRun?.execution_profile;
  const mirrorToGateway = Boolean(
    (data.decisionTrace?.execution_profile ?? latestPaperRun?.execution_profile)?.mirror_to_gateway,
  );
  const deskPositions = useMemo(
    () => deskPositionsFromRuntimeTruth(runtime.positions),
    [runtime.positions],
  );
  const deskOrders = useMemo(
    () => deskOrdersFromRuntimeTruth(runtime.snapshot),
    [runtime.snapshot],
  );
  const activeRuntimeDecisions = useMemo(
    () => runtime.decisions.filter((item) => canarySymbols.includes(platformSymbol(item.symbol))),
    [canarySymbols, runtime.decisions],
  );
  const runtimePositionsError = runtime.positions?.exchange?.status === "unavailable"
    ? runtime.positions.exchange.error ?? "交易所持仓暂不可用"
    : "";
  const runtimeOrdersError = runtime.snapshot?.exchange?.status === "unavailable"
    ? runtime.snapshot.exchange.error ?? "交易所订单暂不可用"
    : "";
  const latestPosition = useMemo(
    () => (deskPositions ?? []).find((position) => position.symbol === symbol && Math.abs(Number(position.quantity)) > 0),
    [deskPositions, symbol],
  );
  const latestPrice = Number(data.snapshot?.perp_last_price ?? data.snapshot?.spot_last_price ?? data.latestKline?.close ?? 0);

  const updateSelection = (next) => {
    const params = new URLSearchParams(searchParams);
    if (next.symbol) params.set("symbol", next.symbol);
    if (next.perp_symbol) params.set("perp_symbol", next.perp_symbol);
    if (next.timeframe) params.set("timeframe", next.timeframe);
    setSearchParams(params, { replace: false });
  };

  const handleSelectSymbol = (nextSymbol, nextPerpSymbol) => {
    updateSelection({ symbol: nextSymbol, perp_symbol: nextPerpSymbol || `${nextSymbol}:USDT` });
  };

  const handleAction = async (type, payload = {}) => {
    setActionMessage("");
    try {
      if (type === "manualOrder") {
        await request("/api/v1/execution/manual-orders", { method: "POST", body: JSON.stringify(payload) });
        setActionMessage("Paper 订单已通过风控并写入审计。");
      }
      if (type === "closePosition") {
        await request("/api/v1/execution/close-position", { method: "POST", body: JSON.stringify(payload) });
        setActionMessage("平仓请求已按 close-only 处理。");
      }
      if (type === "adjustLeverage") {
        await request("/api/v1/execution/adjust-leverage", { method: "POST", body: JSON.stringify(payload) });
        setActionMessage("杠杆调整已记录。");
      }
      if (type === "cancelOrder") {
        await request("/api/v1/execution/cancel-order", { method: "POST", body: JSON.stringify(payload) });
        setActionMessage("撤单已写入订单审计。");
      }
      if (type === "runAllCycles") {
        const result = await request("/api/v1/execution/paper-runs/auto-cycle-all", {
          method: "POST",
          body: JSON.stringify({ symbols: canarySymbols, max_symbols: canarySymbols.length, timeframe, enable_decision_veto: true }),
        });
        setActionMessage(`自动 cycle 已执行：${result.paper_runs} 个 PaperRun。`);
      }
      if (type === "toggleGatewayMirror") {
        if (!autoPaperRunId) {
          throw new Error("没有可配置的 PaperRun");
        }
        await request(`/api/v1/execution/paper-runs/${autoPaperRunId}/execution-profile`, {
          method: "PATCH",
          body: JSON.stringify({
            mirror_to_gateway: Boolean(payload.enabled),
            execution_mode: payload.enabled ? "binance_testnet" : "local_paper",
          }),
        });
        setActionMessage(payload.enabled ? "Testnet 镜像下单已开启。" : "Testnet 镜像下单已关闭。");
      }
      if (type === "saveAutoSettings") {
        if (!autoPaperRunId) {
          throw new Error("没有可配置的自动 PaperRun");
        }
        await request(`/api/v1/execution/paper-runs/${autoPaperRunId}/auto-settings`, {
          method: "PATCH",
          body: JSON.stringify(payload),
        });
        setActionMessage("自动开单设置已保存，并同步到风控档与策略规则。");
      }
      if (type === "carryBacktest") {
        const now = new Date();
        const start = new Date(now.getTime() - 16 * 60 * 60 * 1000);
        await request("/api/v1/backtests/carry", {
          method: "POST",
          body: JSON.stringify({
            strategy_id: payload.strategy_id,
            spot_symbol: symbol,
            perp_symbol: perpSymbol,
            timeframe,
            start_at: start.toISOString(),
            end_at: now.toISOString(),
          }),
        });
        setActionMessage("Carry 回测已提交。");
      }
      if (type === "testnetAcceptance") {
        const approved = window.confirm(
          "这是基础设施连通性验收，不是自动策略。它会按固定五币 Canary 范围开仓后立即平仓，预计产生 10 笔币安模拟盘成交。确认执行吗？",
        );
        if (!approved) return;
        const result = await request("/api/v1/execution/testnet-acceptance-runs", {
          method: "POST",
          body: JSON.stringify({ idempotency_key: `ui-acceptance-${Date.now()}` }),
        });
        setActionMessage(`基础设施验收：${result.run_status}，已记录 ${result.result?.filled_order_count ?? 0} 笔非策略成交。`);
      }
      if (type === "carryExecution") {
        const result = await request("/api/v1/execution/carry-executions", {
          method: "POST",
          body: JSON.stringify({
            symbol,
            perp_symbol: perpSymbol,
            notional_usdt: 1000,
            close_immediately: true,
            idempotency_key: `ui-carry-${Date.now()}`,
          }),
        });
        setActionMessage(`双腿 Carry：${result.run_status} / ${result.carry_state}。`);
      }
      await data.refresh();
    } catch (err) {
      setActionMessage(`操作失败：${err.message}`);
    }
  };

  return (
    <AppShell
      overview={data.overview}
      snapshot={data.snapshot}
      tradingStatus={tradingStatusFromRuntimeSnapshot(runtime.snapshot)}
      streamStatus={data.streamStatus}
      error={data.error}
    >
      {data.loading ? <div className="loading-line">正在加载交易台数据...</div> : null}
      {actionMessage ? <div className="action-line">{actionMessage}</div> : null}
      <TradingSummaryHero
        account={account}
        positions={deskPositions}
        orders={deskOrders}
        decisions={activeRuntimeDecisions}
        noTradeSummary={runtime.noTradeSummary}
        tradingStatus={tradingStatusFromRuntimeSnapshot(runtime.snapshot)}
        globalRiskStatus={riskStatusFromRuntime(runtime.reconciliation, runtime.noTradeSummary)}
        streamStatus={data.streamStatus}
        selectedSymbol={symbol}
        lastSuccessAt={runtime.lastSuccessAt}
      />
      <MarketList
          universe={data.universe}
          universeStatus={data.universeStatus}
          canarySymbols={canarySymbols}
          selectedSymbol={symbol}
        onSelect={handleSelectSymbol}
      />
      <MarketHeader
        snapshot={data.snapshot}
        symbol={symbol}
        perpSymbol={perpSymbol}
        timeframe={timeframe}
        feedStatus={data.feedStatus}
        onTimeframeChange={(nextTimeframe) => updateSelection({ timeframe: nextTimeframe })}
      />
      <ExchangePositionHint
        positions={deskPositions ?? []}
        selectedSymbol={symbol}
        onSelect={handleSelectSymbol}
      />
      <section className="trading-workspace-grid">
        <div className="chart-rail">
          <KlinePanel
            candles={data.candles}
            latestKline={data.latestKline}
            snapshotVersion={data.candleSnapshotVersion}
            orders={deskOrders ?? []}
            symbol={symbol}
            timeframe={timeframe}
            streamStatus={data.streamStatus}
          />
        </div>
        <div className="insight-rail">
          <WhyNoTrade decisions={activeRuntimeDecisions} noTradeSummary={runtime.noTradeSummary} />
        </div>
      </section>
      <TradingRecordsWorkspace tabs={[
        { id: "positions", label: "持仓", count: deskPositions?.length ?? null, content: <DataState loading={runtime.positions == null} error={runtimePositionsError} hasData={deskPositions != null} emptyLabel="持仓状态待确认"><PositionsTable positions={deskPositions ?? []} /></DataState> },
        { id: "orders", label: "订单", count: deskOrders?.length ?? null, content: <DataState loading={runtime.snapshot == null} error={runtimeOrdersError} hasData={deskOrders != null} emptyLabel="订单状态待确认"><OrdersTable orders={deskOrders ?? []} onCancel={(order) => handleAction("cancelOrder", { mode: manualMode, order_execution_id: order.order_execution_id })} /></DataState> },
        { id: "decisions", label: "决策详情", content: <div className="workspace-panel-grid"><DecisionDebugPanel decisionTrace={data.decisionTrace} /><MarketIntelligencePanel signal={data.intelligenceSignal} /></div> },
        { id: "account", label: "账户详情", content: <TestnetAccountPanel account={account} /> },
        { id: "manual", label: "手动 Paper 沙箱", content: <TradingTicket symbol={symbol} timeframe={timeframe} mode={manualMode} manualContext={data.manualContext} latestPosition={latestPosition} latestPrice={latestPrice} onAction={handleAction} /> },
        { id: "automation", label: "策略设置", content: <div className="workspace-panel-grid"><AutoSettingsPanel paperRunId={autoPaperRunId} autoSettings={autoSettings} onSave={(payload) => handleAction("saveAutoSettings", payload)} /><Top20MonitorPanel decisionTrace={data.decisionTrace} tradingStatus={tradingStatusFromRuntimeSnapshot(runtime.snapshot)} /><RejectionFunnelPanel summary={data.decisionTrace?.rejection_summary} /></div> },
        { id: "risk-data", label: "风险与数据", content: <div className="workspace-panel-grid"><MessageSourcesPanel dataSources={data.dataSources} intelligenceSignal={data.intelligenceSignal} riskEvents={data.overview?.risk_events} /><DataSourcesPanel dataSources={data.dataSources} intelligenceSignal={data.intelligenceSignal} /><OrderSyncPanel orderSync={data.orderSync} /></div> },
        { id: "carry", label: "套利工具", content: <div className="workspace-panel-grid"><FundingPanel signal={data.fundingSignal} onBacktest={() => handleAction("carryBacktest", { strategy_id: data.manualContext?.strategy_id ?? "" })} /><ExecutionAcceptancePanel fundingSignal={data.fundingSignal} onRunAcceptance={() => handleAction("testnetAcceptance")} onRunCarry={() => handleAction("carryExecution")} /></div> },
      ]} />
    </AppShell>
  );
}
