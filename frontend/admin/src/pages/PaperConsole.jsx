import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { request } from "../api/client";
import { AppShell } from "../components/Common";
import { KlinePanel, MarketHeader } from "../components/MarketPanels";
import {
  AutoSettingsPanel,
  DecisionDebugPanel,
  DataSourcesPanel,
  MarketIntelligencePanel,
  MessageSourcesPanel,
  OrderSyncPanel,
  Top20MonitorPanel,
} from "../components/RuntimePanels";
import {
  BinanceSyncHero,
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
import { useConsoleData } from "../hooks/useConsoleData";

const DEFAULT_SYMBOL = "BTC/USDT";
const DEFAULT_PERP = "BTC/USDT:USDT";
const DEFAULT_TIMEFRAME = "1m";

export function PaperConsole() {
  const [searchParams, setSearchParams] = useSearchParams();
  const symbol = searchParams.get("symbol") || DEFAULT_SYMBOL;
  const perpSymbol = searchParams.get("perp_symbol") || DEFAULT_PERP;
  const timeframe = searchParams.get("timeframe") || DEFAULT_TIMEFRAME;
  const [actionMessage, setActionMessage] = useState("");
  const data = useConsoleData(symbol, perpSymbol, timeframe);
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
  const latestPosition = useMemo(
    () => (data.overview?.positions ?? []).find((position) => position.symbol === symbol && Math.abs(Number(position.quantity)) > 0),
    [data.overview?.positions, symbol],
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
            execution_mode: payload.enabled ? "binance_simulation_first" : "paper_only",
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
      <ModeBanner status={data.tradingStatus} />
      <BinanceSyncHero account={data.testnetAccount} />
      <MarketHeader
        snapshot={data.snapshot}
        symbol={symbol}
        perpSymbol={perpSymbol}
        timeframe={timeframe}
        feedStatus={data.feedStatus}
        onTimeframeChange={(nextTimeframe) => updateSelection({ timeframe: nextTimeframe })}
      />
      <section className="terminal-grid">
        <div className="market-rail">
          <MarketList universe={data.universe} selectedSymbol={symbol} onSelect={handleSelectSymbol} />
        </div>
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
          <section className="records-grid compact-records">
            <PositionsTable positions={data.overview?.positions} />
            <OrdersTable
              orders={data.overview?.orders}
              onCancel={(order) => handleAction("cancelOrder", { mode, order_execution_id: order.order_execution_id })}
            />
          </section>
        </div>
        <div className="book-rail">
          <OrderBookPanel orderBook={data.orderBook} snapshot={data.snapshot} />
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
      <section className="execution-grid">
        <TestnetAccountPanel account={data.testnetAccount} />
        <RuntimeControlPanel
          streamStatus={data.streamStatus}
          tradingStatus={data.tradingStatus}
          mirrorToGateway={mirrorToGateway}
          onMirrorToggle={(enabled) => handleAction("toggleGatewayMirror", { enabled })}
          onRunCycle={() => handleAction("runAllCycles")}
        />
        <AutoSettingsPanel
          paperRunId={autoPaperRunId}
          autoSettings={autoSettings}
          onSave={(payload) => handleAction("saveAutoSettings", payload)}
        />
        <FundingPanel
          signal={data.fundingSignal}
          onBacktest={() => handleAction("carryBacktest", { strategy_id: data.manualContext?.strategy_id ?? "" })}
        />
        <Top20MonitorPanel decisionTrace={data.decisionTrace} tradingStatus={data.tradingStatus} />
        <DataSourcesPanel dataSources={data.dataSources} intelligenceSignal={data.intelligenceSignal} />
        <MessageSourcesPanel
          dataSources={data.dataSources}
          intelligenceSignal={data.intelligenceSignal}
          riskEvents={data.overview?.risk_events}
        />
        <OrderSyncPanel orderSync={data.orderSync} />
        <MarketIntelligencePanel signal={data.intelligenceSignal} />
        <DecisionDebugPanel decisionTrace={data.decisionTrace} />
      </section>
    </AppShell>
  );
}
