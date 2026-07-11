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
  const [showIdeaForm, setShowIdeaForm] = useState(false);
  const [ideaForm, setIdeaForm] = useState({
    title: "",
    source: "manual_note",
    symbol: "BTC/USDT",
    hypothesis_summary: "",
    rationale: "",
  });

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

  const createIdea = useMutation({
    mutationFn: (payload) => request("/api/v1/strategies/ideas", { method: "POST", body: JSON.stringify(payload) }),
    onSuccess: () => {
      setActionMessage("研究想法已登记，可以继续晋升为规则草稿。");
      setIdeaForm({ title: "", source: "manual_note", symbol: "BTC/USDT", hypothesis_summary: "", rationale: "" });
      setShowIdeaForm(false);
      queryClient.invalidateQueries({ queryKey: ["strategy-ideas"] });
    },
    onError: (err) => setActionMessage(`登记想法失败：${err.message}`),
  });

  const promoteIdea = useMutation({
    mutationFn: (ideaId) => request(`/api/v1/strategies/ideas/${ideaId}/drafts`, { method: "POST", body: "{}" }),
    onSuccess: () => {
      setActionMessage("研究想法已晋升为规则草稿。");
      queryClient.invalidateQueries({ queryKey: ["strategy-ideas"] });
      queryClient.invalidateQueries({ queryKey: ["strategy-drafts"] });
    },
    onError: (err) => setActionMessage(`晋升草稿失败：${err.message}`),
  });

  const submitIdea = (event) => {
    event.preventDefault();
    if (!ideaForm.title.trim() || !ideaForm.hypothesis_summary.trim()) {
      setActionMessage("请填写想法标题和可验证的核心假设。");
      return;
    }
    createIdea.mutate({
      title: ideaForm.title.trim(),
      source: ideaForm.source.trim() || "manual_note",
      market: "crypto_perp",
      symbol_scope: [ideaForm.symbol.trim() || "BTC/USDT"],
      hypothesis_summary: ideaForm.hypothesis_summary.trim(),
      rationale: ideaForm.rationale.trim() || null,
      intake_bucket: "rule_candidate",
    });
  };

  return (
    <main className="app-shell page-shell">
      <header className="page-header">
        <p className="eyebrow">Strategy Layer</p>
        <h1>策略库</h1>
      </header>
      {actionMessage ? <div className="action-line">{actionMessage}</div> : null}
      <section className="form-row">
        <button type="button" onClick={() => setShowIdeaForm((current) => !current)}>
          {showIdeaForm ? "收起研究想法" : "登记研究想法"}
        </button>
      </section>
      {showIdeaForm ? (
        <section className="exchange-panel form-panel">
          <div className="panel-title"><h2>研究想法登记</h2></div>
          <form className="form-grid" onSubmit={submitIdea}>
            <input aria-label="想法标题" placeholder="想法标题" value={ideaForm.title} onChange={(event) => setIdeaForm((current) => ({ ...current, title: event.target.value }))} />
            <input aria-label="来源" placeholder="来源" value={ideaForm.source} onChange={(event) => setIdeaForm((current) => ({ ...current, source: event.target.value }))} />
            <input aria-label="交易标的" placeholder="交易标的" value={ideaForm.symbol} onChange={(event) => setIdeaForm((current) => ({ ...current, symbol: event.target.value }))} />
            <textarea aria-label="可验证核心假设" placeholder="可验证核心假设" value={ideaForm.hypothesis_summary} onChange={(event) => setIdeaForm((current) => ({ ...current, hypothesis_summary: event.target.value }))} />
            <textarea aria-label="研究依据" placeholder="研究依据（可选）" value={ideaForm.rationale} onChange={(event) => setIdeaForm((current) => ({ ...current, rationale: event.target.value }))} />
            <button type="submit" disabled={createIdea.isPending}>{createIdea.isPending ? "登记中" : "登记"}</button>
          </form>
        </section>
      ) : null}
      <section className="records-grid">
        <StrategyTable
          title="已物化策略"
          rows={strategyRows}
          columns={["strategy_id", "market", "timeframe", "risk_level", "paper_status"]}
          onRowClick={(row) => row.strategy_id && navigate(`/strategies/${row.strategy_id}`)}
        />
        <StrategyTable
          title="规则草稿"
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
              <th>操作</th>
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
              <td><button type="button" onClick={() => promoteIdea.mutate(item.idea_id)} disabled={promoteIdea.isPending}>晋升草稿</button></td>
            </tr>
            )) : <tr><td colSpan="6">暂无策略想法。先登记一个可验证的研究想法。</td></tr>}
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
