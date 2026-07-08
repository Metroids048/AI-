import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";

import { request } from "../api/client";
import { DetailHeader, JsonPanel, StatusBadge } from "../components/DetailPanels";
import { Metric } from "../components/Common";
import { formatNumber, formatPercent, formatTime } from "../utils/format";

export function BacktestDetail() {
  const { runId } = useParams();
  const run = useQuery({
    queryKey: ["backtest-run", runId],
    queryFn: () => request(`/api/v1/backtests/${runId}`),
    enabled: Boolean(runId),
  });
  const report = useQuery({
    queryKey: ["backtest-report", runId],
    queryFn: () => request(`/api/v1/validation/reports/${runId}`),
    enabled: Boolean(runId),
  });
  const eligibility = useQuery({
    queryKey: ["backtest-eligibility", runId],
    queryFn: () => request(`/api/v1/backtests/${runId}/eligibility`),
    enabled: Boolean(runId),
    retry: false,
  });

  const metrics = run.data?.metrics_summary ?? {};

  return (
    <main className="app-shell page-shell">
      <DetailHeader
        eyebrow="Validation Layer"
        title={`回测 ${runId}`}
        backTo="/validation"
      />
      {run.isError ? <div className="action-line">加载失败：{run.error.message}</div> : null}
      <section className="funding-metrics">
        <Metric label="状态" value={<StatusBadge value={run.data?.run_status} />} />
        <Metric label="策略" value={run.data?.strategy_id ?? "-"} />
        <Metric label="Sharpe" value={formatNumber(metrics.sharpe, 3)} />
        <Metric label="Profit Factor" value={formatNumber(metrics.profit_factor, 3)} />
        <Metric label="Max Drawdown" value={formatPercent(metrics.max_drawdown)} />
        <Metric label="Expectancy" value={formatNumber(metrics.expectancy, 4)} />
        <Metric label="Deflated Sharpe" value={formatNumber(metrics.deflated_sharpe, 3)} />
        <Metric label="创建时间" value={formatTime(run.data?.created_at)} />
      </section>
      <section className="records-grid">
        <JsonPanel title="Eligibility" value={eligibility.data ?? { status: eligibility.isError ? "not_ready" : "pending" }} />
        <JsonPanel title="Validation Report" value={report.data ?? {}} />
      </section>
    </main>
  );
}
