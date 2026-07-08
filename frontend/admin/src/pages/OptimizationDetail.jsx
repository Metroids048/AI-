import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";

import { request } from "../api/client";
import { DetailHeader, JsonPanel, StatusBadge } from "../components/DetailPanels";
import { Metric } from "../components/Common";
import { formatTime } from "../utils/format";

export function OptimizationDetail() {
  const { runId } = useParams();
  const run = useQuery({
    queryKey: ["optimization-run", runId],
    queryFn: () => request(`/api/v1/optimizations/${runId}`),
    enabled: Boolean(runId),
  });

  return (
    <main className="app-shell page-shell">
      <DetailHeader
        eyebrow="Validation Layer"
        title={`优化 ${runId}`}
        backTo="/validation"
      />
      {run.isError ? <div className="action-line">加载失败：{run.error.message}</div> : null}
      <section className="funding-metrics">
        <Metric label="状态" value={<StatusBadge value={run.data?.run_status} />} />
        <Metric label="策略" value={run.data?.strategy_id ?? "-"} />
        <Metric label="方法" value={run.data?.optimization_method ?? "-"} />
        <Metric label="版本" value={run.data?.version_id ?? "-"} />
        <Metric label="创建时间" value={formatTime(run.data?.created_at)} />
      </section>
      <JsonPanel title="Best Candidate Summary" value={run.data?.best_candidate_summary ?? {}} />
    </main>
  );
}
