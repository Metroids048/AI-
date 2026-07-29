import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { fetchPositions } from "../api/automatedTrading";
import { request } from "../api/client";
import { ExchangeVsLocal, RuntimeOverview, WhyNoTrade } from "../components/AutomatedTrading";
import { AppShell } from "../components/Common";
import { KlinePanel, MarketHeader } from "../components/MarketPanels";
import { ExecutionAcceptancePanel, TradingRecordsWorkspace } from "../components/TradingRecordsWorkspace";
import { RuntimeTruthPanel } from "../components/RuntimeTruthPanel";
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
  BinanceSyncHero,
  ExchangePositionHint,
  FundingPanel,
  MarketList,
  ModeBanner,
  OrderBookPanel,
  OrdersTable,
  PositionsTable,
  RecentTradesPanel,
  RuntimeControlPanel,
  TestnetAccountPanel,
  TradingTicket,
} from "../components/TradingConsolePanels";
import { useAutomatedTradingRuntime } from "../hooks/useAutomatedTradingRuntime";
import { useConsoleData } from "../hooks/useConsoleData";
import { useRuntimeTruth } from "../hooks/useRuntimeTruth";

const DEFAULT_SYMBOL = "BTC/USDT";
const DEFAULT_PERP = "BTC/USDT:USDT";
const DEFAULT_TIMEFRAME = "1m";

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

export function PaperConsole() {
  const [searchParams, setSearchParams] = useSearchParams();
  const symbol = searchParams.get("symbol") || DEFAULT_SYMBOL;
  const perpSymbol = searchParams.get("perp_symbol") || DEFAULT_PERP;
  const timeframe = searchParams.get("timeframe") || DEFAULT_TIMEFRAME;
  const [actionMessage, setActionMessage] = useState("");
  const data = useConsoleData(symbol, perpSymbol, timeframe);
  const runtimeTruth = useRuntimeTruth(symbol);
  const v2Runtime = useAutomatedTradingRuntime();
  const [v2Positions, setV2Positions] = useState(null);

  useEffect(() => {
    let cancelled = false;
    fetchPositions()
      .then((payload) => {
        if (!cancelled) setV2Positions(payload);
      })
      .catch(() => {
        if (!cancelled) setV2Positions(null);
      });
    return () => {
      cancelled = true;
    };
  }, [v2Runtime.lastSuccessAt]);
  const mode = "paper";
  const latestPaperRun = useMemo(
    () => (Array.isArray(data.overview?.paper_runs) ? data.overview.paper_runs.at(-1) : null),
    [data.overview?.paper_runs],
  );
  const autoPaperRunId = data.decisionTrace?.paper_run_id ?? latestPaperRun?.paper_run_id;
  const autoSettings = data.decisionTrace?.auto_settings ?? latestPaperRun?.execution_profile;
  const mirrorToGateway = Boolean(
    (data.decisionTrace?.execution_profile ?? latestPaperRun?.execution_profile)?.mirror_to_gateway,
  );
  const deskPositions = useMemo(() => {
    const fromExchange = deskPositionsFromAccount(data.testnetAccount);
    if (fromExchange) return fromExchange;
    return (data.overview?.positions ?? []).map((pos) => {
      const qty = Math.abs(Number(pos.quantity) || 0);
      const markPrice = Number(pos.mark_price || pos.entry_price || 0);
      const notional = pos.notional_usdt ?? (qty * markPrice > 0 ? qty * markPrice : null);
      const leverage = pos.leverage ?? null;
      const margin = pos.margin_usdt ?? (notional && leverage ? notional / leverage : null);
      return { ...pos, notional_usdt: notional, margin_usdt: margin, leverage };
    });
  }, [data.testnetAccount, data.overview?.positions]);
  const deskOrders = useMemo(() => {
    const fromExchange = deskOrdersFromAccount(data.testnetAccount);
    if (fromExchange && fromExchange.length) return fromExchange;
    return data.overview?.orders ?? [];
  }, [data.testnetAccount, data.overview?.orders]);
  const latestPosition = useMemo(
    () => deskPositions.find((position) => position.symbol === symbol && Math.abs(Number(position.quantity)) > 0),
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
          body: JSON.stringify({ symbols: [], max_symbols: Number(autoSettings?.max_symbols ?? 20), timeframe, enable_decision_veto: true }),
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
          "这是基础设施连通性验收，不是自动策略。它会按固定 20 个币种开仓后立即平仓，预计产生 40 笔币安模拟盘成交。确认执行吗？",
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
      tradingStatus={data.tradingStatus}
      streamStatus={data.streamStatus}
      error={data.error}
    >
      {data.loading ? <div className="loading-line">正在加载交易台数据...</div> : null}
      {actionMessage ? <div className="action-line">{actionMessage}</div> : null}
      <MarketList
        universe={data.universe}
        universeStatus={data.universeStatus}
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
      <BinanceSyncHero account={data.testnetAccount} />
      <RuntimeTruthPanel runtime={runtimeTruth} symbol={symbol} />
      <div className="workspace-panel-grid">
        <RuntimeOverview
          snapshot={v2Runtime.snapshot}
          error={v2Runtime.error}
          lastSuccessAt={v2Runtime.lastSuccessAt}
        />
        <WhyNoTrade decisions={v2Runtime.snapshot?.latest_decisions ?? []} />
        <ExchangeVsLocal positionsData={v2Positions} />
      </div>
      <ExchangePositionHint
        positions={deskPositions}
        selectedSymbol={symbol}
        onSelect={handleSelectSymbol}
      />
      <section className="trading-workspace-grid">
        <div className="chart-rail">
          <KlinePanel
            candles={data.candles}
            latestKline={data.latestKline}
            snapshotVersion={data.candleSnapshotVersion}
            orders={data.overview?.orders}
            symbol={symbol}
            timeframe={timeframe}
            streamStatus={data.streamStatus}
          />
        </div>
        <div className="book-rail">
          <OrderBookPanel orderBook={data.orderBook} symbol={perpSymbol} />
          <RecentTradesPanel trades={data.trades} symbol={perpSymbol} />
        </div>
        <div className="ticket-rail">
          <TradingTicket
            symbol={symbol}
            timeframe={timeframe}
            mode={mode}
            manualContext={data.manualContext}
            latestPosition={latestPosition}
            latestPrice={latestPrice}
            onAction={handleAction}
          />
        </div>
      </section>
      <TradingRecordsWorkspace tabs={[
        { id: "positions", label: "持仓", count: deskPositions.length, content: <PositionsTable positions={deskPositions} /> },
        { id: "orders", label: "订单", count: deskOrders.length, content: <OrdersTable orders={deskOrders} onCancel={(order) => handleAction("cancelOrder", { mode, order_execution_id: order.order_execution_id })} /> },
        { id: "account", label: "币安账户", content: <><BinanceSyncHero account={data.testnetAccount} /><TestnetAccountPanel account={data.testnetAccount} /></> },
        { id: "automation", label: "自动交易", content: <><ModeBanner status={data.tradingStatus} account={data.testnetAccount} /><div className="workspace-panel-grid"><RuntimeControlPanel streamStatus={data.streamStatus} tradingStatus={data.tradingStatus} mirrorToGateway={mirrorToGateway} onMirrorToggle={(enabled) => handleAction("toggleGatewayMirror", { enabled })} onRunCycle={() => handleAction("runAllCycles")} /><AutoSettingsPanel paperRunId={autoPaperRunId} autoSettings={autoSettings} onSave={(payload) => handleAction("saveAutoSettings", payload)} /><Top20MonitorPanel decisionTrace={data.decisionTrace} tradingStatus={data.tradingStatus} /><RejectionFunnelPanel summary={data.decisionTrace?.rejection_summary} /></div></> },
        { id: "carry", label: "套利", content: <div className="workspace-panel-grid"><FundingPanel signal={data.fundingSignal} onBacktest={() => handleAction("carryBacktest", { strategy_id: data.manualContext?.strategy_id ?? "" })} /><ExecutionAcceptancePanel fundingSignal={data.fundingSignal} onRunAcceptance={() => handleAction("testnetAcceptance")} onRunCarry={() => handleAction("carryExecution")} /></div> },
        { id: "decision", label: "决策链", content: <div className="workspace-panel-grid"><DecisionDebugPanel decisionTrace={data.decisionTrace} /><MarketIntelligencePanel signal={data.intelligenceSignal} /></div> },
        { id: "risk-data", label: "风险与数据", content: <div className="workspace-panel-grid"><MessageSourcesPanel dataSources={data.dataSources} intelligenceSignal={data.intelligenceSignal} riskEvents={data.overview?.risk_events} /><DataSourcesPanel dataSources={data.dataSources} intelligenceSignal={data.intelligenceSignal} /><OrderSyncPanel orderSync={data.orderSync} /></div> },
      ]} />
    </AppShell>
  );
}
