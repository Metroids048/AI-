import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useParams } from "react-router-dom";

import { request } from "../api/client";
import { DetailHeader, JsonPanel, StatusBadge, ActionMessage } from "../components/DetailPanels";
import { Metric } from "../components/Common";
import { asArray, formatTime } from "../utils/format";

const STATUS_OPTIONS = [
  "drafting",
  "ready_for_codegen",
  "code_generated",
  "in_backtest",
  "backtest_rejected",
  "paper_candidate",
  "in_paper_run",
  "paper_rejected",
  "live_candidate",
  "in_live_run",
  "active",
  "paused",
  "retired",
];

export function StrategyDetail() {
  const { strategyId } = useParams();
  const queryClient = useQueryClient();
  const [actionMessage, setActionMessage] = useState("");
  const [nextStatus, setNextStatus] = useState("");

  const strategy = useQuery({
    queryKey: ["strategy-detail", strategyId],
    queryFn: () => request(`/api/v1/strategies/${strategyId}`),
    enabled: Boolean(strategyId),
  });
  const versions = useQuery({
    queryKey: ["strategy-versions", strategyId],
    queryFn: () => request(`/api/v1/strategies/versions?strategy_id=${encodeURIComponent(strategyId)}`),
    enabled: Boolean(strategyId),
  });

  const versionRows = asArray(versions.data?.items);

  const handleStatusUpdate = async () => {
    if (!strategyId || !nextStatus) return;
    try {
      await request(`/api/v1/strategies/${strategyId}`, {
        method: "PUT",
        body: JSON.stringify({ strategy_status: nextStatus }),
      });
      setActionMessage("策略状态已更新。");
      await queryClient.invalidateQueries({ queryKey: ["strategy-detail", strategyId] });
    } catch (err) {
      setActionMessage(`状态更新失败：${err.message}`);
    }
  };

  const data = strategy.data ?? {};

  return (
    <main className="app-shell page-shell">
      <DetailHeader eyebrow="Strategy Layer" title={data.strategy_key ?? strategyId} backTo="/strategies" />
      <ActionMessage message={actionMessage} />
      {strategy.isError ? <div className="action-line">加载失败：{strategy.error.message}</div> : null}
      <section className="funding-metrics">
        <Metric label="策略 ID" value={data.strategy_id ?? "-"} />
        <Metric label="市场" value={data.market ?? "-"} />
        <Metric label="周期" value={data.timeframe ?? "-"} />
        <Metric label="风险" value={data.risk_level ?? "-"} />
        <Metric label="回测" value={<StatusBadge value={data.backtest_status} />} />
        <Metric label="模拟" value={<StatusBadge value={data.paper_status} />} />
        <Metric label="实盘" value={<StatusBadge value={data.live_status} />} />
        <Metric label="更新时间" value={formatTime(data.updated_at)} />
      </section>
      <section className="exchange-panel table-panel">
        <div className="panel-title"><h2>核心假设</h2></div>
        <p>{data.core_thesis ?? "-"}</p>
      </section>
      <section className="records-grid">
        <JsonPanel title="规则" value={data.rules ?? {}} />
        <JsonPanel title="失败原因" value={data.failure_reasons ?? []} />
      </section>
      <JsonPanel title="迭代历史" value={data.iteration_history ?? []} />
      <section className="exchange-panel table-panel">
        <div className="panel-title"><h2>版本历史</h2><span>{versionRows.length}</span></div>
        <table>
          <thead>
            <tr>
              <th>Version</th>
              <th>Label</th>
              <th>Summary</th>
              <th>Created</th>
            </tr>
          </thead>
          <tbody>
            {versionRows.length ? versionRows.map((item) => (
              <tr key={item.version_id}>
                <td>{item.version_id}</td>
                <td>{item.version_label}</td>
                <td>{item.change_summary}</td>
                <td>{formatTime(item.created_at)}</td>
              </tr>
            )) : <tr><td colSpan="4">暂无版本记录</td></tr>}
          </tbody>
        </table>
      </section>
      <section className="exchange-panel form-panel">
        <div className="panel-title"><h2>状态流转</h2></div>
        <div className="form-row">
          <select value={nextStatus || data.strategy_status || ""} onChange={(event) => setNextStatus(event.target.value)}>
            {STATUS_OPTIONS.map((status) => <option key={status} value={status}>{status}</option>)}
          </select>
          <button type="button" onClick={handleStatusUpdate}>更新状态</button>
        </div>
      </section>
    </main>
  );
}
