import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { useState } from "react";

import { request } from "../api/client";
import { asArray } from "../utils/format";

function ideaSummary(item) {
  return item.hypothesis_summary ?? item.core_thesis ?? "-";
}

export function StrategyLibrary() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [actionMessage, setActionMessage] = useState("");

  const strategies = useQuery({ queryKey: ["strategies"], queryFn: () => request("/api/v1/strategies"), staleTime: 15000 });
  const drafts = useQuery({ queryKey: ["strategy-drafts"], queryFn: () => request("/api/v1/strategies/drafts"), staleTime: 15000 });
  const ideas = useQuery({ queryKey: ["strategy-ideas"], queryFn: () => request("/api/v1/strategies/ideas"), staleTime: 15000 });
  const strategyRows = asArray(strategies.data?.items);
  const draftRows = asArray(drafts.data?.items);
  const ideaRows = asArray(ideas.data?.items);

  const materializeDraft = useMutation({
    mutationFn: (draftId) => request(`/api/v1/strategies/${draftId}/materialize`, { method: "POST", body: "{}" }),
    onSuccess: () => {
      setActionMessage("草稿已物化为策略。");
      queryClient.invalidateQueries({ queryKey: ["strategies"] });
      queryClient.invalidateQueries({ queryKey: ["strategy-drafts"] });
    },
    onError: (err) => setActionMessage(`物化失败：${err.message}`),
  });

  return (
    <main className="app-shell page-shell">
      <header className="page-header">
        <p className="eyebrow">Strategy Layer</p>
        <h1>策略库</h1>
      </header>
      {actionMessage ? <div className="action-line">{actionMessage}</div> : null}
      <section className="records-grid">
        <StrategyTable
          title="已物化策略"
          rows={strategyRows}
          columns={["strategy_id", "market", "timeframe", "risk_level", "paper_status"]}
          onRowClick={(row) => row.strategy_id && navigate(`/strategies/${row.strategy_id}`)}
        />
        <StrategyTable
          title="策略草稿"
          rows={draftRows}
          columns={["draft_id", "source", "market", "timeframe", "draft_status"]}
          renderActions={(row) => (
            <button
              type="button"
              onClick={() => materializeDraft.mutate(row.draft_id)}
              disabled={materializeDraft.isPending}
            >
              物化
            </button>
          )}
        />
      </section>
      <section className="exchange-panel table-panel">
        <div className="panel-title"><h2>研究想法</h2><span>{ideaRows.length}</span></div>
        <table>
          <thead>
            <tr>
              <th>ID</th>
              <th>来源</th>
              <th>市场</th>
              <th>状态</th>
              <th>核心假设</th>
            </tr>
          </thead>
          <tbody>
            {ideaRows.length ? ideaRows.map((item) => (
              <tr key={item.idea_id}>
                <td>{item.idea_id}</td>
                <td>{item.source}</td>
                <td>{item.market}</td>
                <td>{item.idea_status ?? item.intake_bucket ?? "-"}</td>
                <td>{ideaSummary(item)}</td>
              </tr>
            )) : <tr><td colSpan="5">暂无策略想法</td></tr>}
          </tbody>
        </table>
      </section>
    </main>
  );
}

function StrategyTable({ title, rows, columns, onRowClick, renderActions }) {
  return (
    <div className="exchange-panel table-panel">
      <div className="panel-title"><h2>{title}</h2><span>{rows.length}</span></div>
      <table>
        <thead>
          <tr>
            {columns.map((column) => <th key={column}>{column}</th>)}
            {renderActions ? <th>操作</th> : null}
          </tr>
        </thead>
        <tbody>
          {rows.length ? rows.map((row, index) => (
            <tr
              key={row.strategy_id ?? row.draft_id ?? index}
              className={onRowClick ? "clickable-row" : undefined}
              onClick={onRowClick ? () => onRowClick(row) : undefined}
            >
              {columns.map((column) => <td key={column}>{String(row[column] ?? "-")}</td>)}
              {renderActions ? <td onClick={(event) => event.stopPropagation()}>{renderActions(row)}</td> : null}
            </tr>
          )) : <tr><td colSpan={columns.length + (renderActions ? 1 : 0)}>暂无记录</td></tr>}
        </tbody>
      </table>
    </div>
  );
}
