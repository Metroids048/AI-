import { useMemo, useState } from "react";

import { request } from "../api/client";
import { AppShell } from "../components/Common";
import { KlinePanel, MarketHeader } from "../components/MarketPanels";
import { OpsReviewPanel } from "../components/OpsPanels";
import { DecisionDebugPanel, RiskEventFeed } from "../components/RuntimePanels";
import {
  FundingPanel,
  MarketList,
  ModeBanner,
  OrderBookPanel,
  OrdersTable,
  PositionsTable,
  RecentTradesPanel,
  RuntimeControlPanel,
  TradingTicket,
} from "../components/TradingConsolePanels";
import { useConsoleData } from "../hooks/useConsoleData";

const DEFAULT_SYMBOL = "BTC/USDT";
const DEFAULT_PERP = "BTC/USDT:USDT";

export function PaperConsole() {
  const [symbol, setSymbol] = useState(DEFAULT_SYMBOL);
  const [perpSymbol, setPerpSymbol] = useState(DEFAULT_PERP);
  const [timeframe, setTimeframe] = useState("1h");
  const [actionMessage, setActionMessage] = useState("");
  const data = useConsoleData(symbol, perpSymbol, timeframe);
  const mode = data.tradingStatus?.mode ?? "paper";
  const latestPosition = useMemo(
    () => (data.overview?.positions ?? []).find((position) => position.symbol === symbol && Math.abs(Number(position.quantity)) > 0),
    [data.overview?.positions, symbol],
  );

  const handleSelectSymbol = (nextSymbol, nextPerpSymbol) => {
    setSymbol(nextSymbol);
    setPerpSymbol(nextPerpSymbol);
  };

  const handleAction = async (type, payload = {}) => {
    setActionMessage("");
    try {
      if (type === "manualOrder") {
        await request("/api/v1/execution/manual-orders", { method: "POST", body: JSON.stringify(payload) });
        setActionMessage("手动订单已通过风控并写入审计。");
      }
      if (type === "closePosition") {
        await request("/api/v1/execution/close-position", { method: "POST", body: JSON.stringify(payload) });
        setActionMessage("平仓请求已按 close-only / reduce-only 处理。");
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
          body: JSON.stringify({ symbols: [symbol], max_symbols: 1, timeframe, enable_decision_veto: true }),
        });
        setActionMessage(`自动 cycle 已执行：${result.paper_runs} 个 PaperRun。`);
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

  const handleResolveRisk = async (riskEventId, resolutionStatus) => {
    if (!riskEventId) return;
    try {
      await request(`/api/v1/risk/events/${riskEventId}/resolution`, {
        method: "PATCH",
        body: JSON.stringify({ resolution_status: resolutionStatus }),
      });
      setActionMessage(resolutionStatus === "resolved" ? "风险事件已恢复。" : "风险事件已确认。");
      await data.refresh();
    } catch (err) {
      setActionMessage(`风险事件操作失败：${err.message}`);
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
      <MarketHeader
        snapshot={data.snapshot}
        symbol={symbol}
        setSymbol={setSymbol}
        perpSymbol={perpSymbol}
        setPerpSymbol={setPerpSymbol}
        timeframe={timeframe}
        setTimeframe={setTimeframe}
      />
      <section className="terminal-grid">
        <div className="left-rail">
          <OrderBookPanel orderBook={data.orderBook} snapshot={data.snapshot} />
        </div>
        <div className="center-stack">
          <KlinePanel candles={data.candles} orders={data.overview?.orders} timeframe={timeframe} streamStatus={data.streamStatus} />
          <TradingTicket symbol={symbol} mode={mode} latestPosition={latestPosition} onAction={handleAction} />
        </div>
        <div className="right-rail">
          <MarketList universe={data.universe} selectedSymbol={symbol} onSelect={handleSelectSymbol} />
          <RecentTradesPanel trades={data.trades} symbol={symbol} />
        </div>
      </section>
      <section className="execution-grid">
        <FundingPanel signal={data.fundingSignal} onBacktest={() => handleAction("carryBacktest", { strategy_id: "" })} />
        <RuntimeControlPanel
          streamStatus={data.streamStatus}
          tradingStatus={data.tradingStatus}
          onRunCycle={() => handleAction("runAllCycles")}
        />
        <DecisionDebugPanel decisionTrace={data.decisionTrace} />
        <RiskEventFeed events={data.overview?.risk_events} onResolve={handleResolveRisk} />
      </section>
      <section className="records-grid">
        <OrdersTable
          orders={data.overview?.orders}
          onCancel={(order) => handleAction("cancelOrder", { mode, order_execution_id: order.order_execution_id })}
        />
        <PositionsTable positions={data.overview?.positions} />
      </section>
      <OpsReviewPanel
        newsItems={data.newsItems}
        macroEvents={data.macroEvents}
        reviews={data.reviews}
        notifications={data.notifications}
      />
    </AppShell>
  );
}
