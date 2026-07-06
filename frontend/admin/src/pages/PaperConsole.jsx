import { useState } from "react";

import { request } from "../api/client";
import { AppShell } from "../components/Common";
import { CarryPanel, KlinePanel, MarketHeader } from "../components/MarketPanels";
import { OpsReviewPanel } from "../components/OpsPanels";
import {
  DecisionDebugPanel,
  OrdersTable,
  PaperRunControls,
  PositionsTable,
  RiskEventFeed,
} from "../components/RuntimePanels";
import { useConsoleData } from "../hooks/useConsoleData";

const DEFAULT_SYMBOL = "BTC/USDT";
const DEFAULT_PERP = "BTC/USDT:USDT";

export function PaperConsole() {
  const [symbol, setSymbol] = useState(DEFAULT_SYMBOL);
  const [perpSymbol, setPerpSymbol] = useState(DEFAULT_PERP);
  const [timeframe, setTimeframe] = useState("1h");
  const [actionMessage, setActionMessage] = useState("");
  const data = useConsoleData(symbol, perpSymbol, timeframe);

  const handleAction = async (type, payload) => {
    setActionMessage("");
    try {
      if (type === "startPaper") {
        await request("/api/v1/execution/paper-runs", { method: "POST", body: JSON.stringify(payload) });
        setActionMessage("Paper run 已提交");
      }
      if (type === "pausePaper") {
        await request(`/api/v1/execution/paper-runs/${payload.paper_run_id}/status`, {
          method: "PATCH",
          body: JSON.stringify({ paper_status: "paused" }),
        });
        setActionMessage("Paper run 已暂停");
      }
      if (type === "autoCycle") {
        await request(`/api/v1/execution/paper-runs/${payload.paper_run_id}/auto-cycle`, {
          method: "POST",
          body: JSON.stringify({ symbols: [symbol], max_symbols: 1, timeframe, enable_decision_veto: false }),
        });
        setActionMessage("Paper cycle 已触发");
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
        setActionMessage("Carry 回测已提交");
      }
      if (type === "createRisk") {
        await request("/api/v1/risk/events", {
          method: "POST",
          body: JSON.stringify({
            event_type: "exchange_incident",
            severity: "high",
            source: "manual_console",
            description: payload.description,
            affected_scope: [symbol],
          }),
        });
        setActionMessage("风险事件已提交");
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
      setActionMessage(resolutionStatus === "resolved" ? "风险事件已恢复" : "风险事件已确认");
      await data.refresh();
    } catch (err) {
      setActionMessage(`风险事件操作失败：${err.message}`);
    }
  };

  return (
    <AppShell overview={data.overview} snapshot={data.snapshot} error={data.error}>
      {data.loading ? <div className="loading-line">正在加载控制台数据</div> : null}
      {actionMessage ? <div className="action-line">{actionMessage}</div> : null}
      <MarketHeader
        snapshot={data.snapshot}
        symbol={symbol}
        setSymbol={setSymbol}
        perpSymbol={perpSymbol}
        setPerpSymbol={setPerpSymbol}
        timeframe={timeframe}
        setTimeframe={setTimeframe}
      />
      <section className="main-grid">
        <KlinePanel candles={data.candles} orders={data.overview?.orders} timeframe={timeframe} />
        <CarryPanel snapshot={data.snapshot} latestBacktests={data.overview?.latest_backtests} />
        <PaperRunControls overview={data.overview} onAction={handleAction} />
      </section>
      <section className="analysis-grid">
        <DecisionDebugPanel decisionTrace={data.decisionTrace} />
        <RiskEventFeed events={data.overview?.risk_events} onResolve={handleResolveRisk} />
      </section>
      <section className="bottom-grid">
        <OrdersTable orders={data.overview?.orders} />
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
