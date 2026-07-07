import { useQuery } from "@tanstack/react-query";

import { request } from "../api/client";
import { asArray } from "../utils/format";

export function StrategyLibrary() {
  const strategies = useQuery({ queryKey: ["strategies"], queryFn: () => request("/api/v1/strategies"), staleTime: 15000 });
  const drafts = useQuery({ queryKey: ["strategy-drafts"], queryFn: () => request("/api/v1/strategies/drafts"), staleTime: 15000 });
  const ideas = useQuery({ queryKey: ["strategy-ideas"], queryFn: () => request("/api/v1/strategies/ideas"), staleTime: 15000 });
  const strategyRows = asArray(strategies.data?.items);
  const draftRows = asArray(drafts.data?.items);
  const ideaRows = asArray(ideas.data?.items);

  return (
    <main className="app-shell page-shell">
      <header className="page-header">
        <p className="eyebrow">Strategy Layer</p>
        <h1>策略库</h1>
      </header>
      <section className="records-grid">
        <StrategyTable title="已物化策略" rows={strategyRows} columns={["strategy_id", "market", "timeframe", "risk_level", "paper_status"]} />
        <StrategyTable title="策略草稿" rows={draftRows} columns={["draft_id", "source", "market", "timeframe", "draft_status"]} />
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
                <td>{item.idea_status}</td>
                <td>{item.core_thesis}</td>
              </tr>
            )) : <tr><td colSpan="5">暂无策略想法</td></tr>}
          </tbody>
        </table>
      </section>
    </main>
  );
}

function StrategyTable({ title, rows, columns }) {
  return (
    <div className="exchange-panel table-panel">
      <div className="panel-title"><h2>{title}</h2><span>{rows.length}</span></div>
      <table>
        <thead>
          <tr>{columns.map((column) => <th key={column}>{column}</th>)}</tr>
        </thead>
        <tbody>
          {rows.length ? rows.map((row, index) => (
            <tr key={row.strategy_id ?? row.draft_id ?? index}>
              {columns.map((column) => <td key={column}>{String(row[column] ?? "-")}</td>)}
            </tr>
          )) : <tr><td colSpan={columns.length}>暂无记录</td></tr>}
        </tbody>
      </table>
    </div>
  );
}
