import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { useState } from "react";

import { request } from "../api/client";
import { asArray } from "../utils/format";

const TABS = [
  ["assets", "策略资产"], ["overview", "策略总览"], ["entry", "开单逻辑"],
  ["exit", "平单逻辑"], ["position", "仓位管理"], ["llm", "LLM 与 RAG 边界"],
  ["sources", "外部策略来源"], ["roadmap", "优化路线图"],
];

function ideaSummary(item) {
  return item.hypothesis_summary ?? item.core_thesis ?? "-";
}

export function StrategyLibrary() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [activeTab, setActiveTab] = useState("overview");
  const [actionMessage, setActionMessage] = useState("");
  const [showIdeaForm, setShowIdeaForm] = useState(false);
  const [ideaForm, setIdeaForm] = useState({ title: "", source: "manual_note", symbol: "BTC/USDT", hypothesis_summary: "", rationale: "" });
  const playbook = useQuery({ queryKey: ["strategy-playbook"], queryFn: () => request("/api/v1/strategy-library/playbook"), staleTime: 60000 });
  const strategies = useQuery({ queryKey: ["strategies"], queryFn: () => request("/api/v1/strategies"), staleTime: 15000 });
  const drafts = useQuery({ queryKey: ["strategy-drafts"], queryFn: () => request("/api/v1/strategies/drafts"), staleTime: 15000 });
  const ideas = useQuery({ queryKey: ["strategy-ideas"], queryFn: () => request("/api/v1/strategies/ideas"), staleTime: 15000 });

  const materializeDraft = useMutation({
    mutationFn: (draftId) => request(`/api/v1/strategies/${draftId}/materialize`, { method: "POST", body: "{}" }),
    onSuccess: () => { setActionMessage("草稿已物化为策略。"); queryClient.invalidateQueries({ queryKey: ["strategies"] }); queryClient.invalidateQueries({ queryKey: ["strategy-drafts"] }); },
    onError: (err) => setActionMessage(`物化失败：${err.message}`),
  });
  const createIdea = useMutation({
    mutationFn: (payload) => request("/api/v1/strategies/ideas", { method: "POST", body: JSON.stringify(payload) }),
    onSuccess: () => { setActionMessage("研究想法已登记，可以继续晋升为规则草稿。"); setIdeaForm({ title: "", source: "manual_note", symbol: "BTC/USDT", hypothesis_summary: "", rationale: "" }); setShowIdeaForm(false); queryClient.invalidateQueries({ queryKey: ["strategy-ideas"] }); },
    onError: (err) => setActionMessage(`登记想法失败：${err.message}`),
  });
  const promoteIdea = useMutation({
    mutationFn: (ideaId) => request(`/api/v1/strategies/ideas/${ideaId}/drafts`, { method: "POST", body: "{}" }),
    onSuccess: () => { setActionMessage("研究想法已晋升为规则草稿。"); queryClient.invalidateQueries({ queryKey: ["strategy-ideas"] }); queryClient.invalidateQueries({ queryKey: ["strategy-drafts"] }); },
    onError: (err) => setActionMessage(`晋升草稿失败：${err.message}`),
  });
  const updateRoadmap = useMutation({
    mutationFn: ({ itemId, status }) => request(`/api/v1/strategy-library/roadmap-items/${itemId}`, { method: "PATCH", body: JSON.stringify({ status, updated_by: "admin_console" }) }),
    onSuccess: () => { setActionMessage("路线图状态已保存。"); queryClient.invalidateQueries({ queryKey: ["strategy-playbook"] }); },
    onError: (err) => { setActionMessage(`路线图状态保存失败：${err.message}`); queryClient.invalidateQueries({ queryKey: ["strategy-playbook"] }); },
  });

  const submitIdea = (event) => {
    event.preventDefault();
    if (!ideaForm.title.trim() || !ideaForm.hypothesis_summary.trim()) { setActionMessage("请填写想法标题和可验证的核心假设。"); return; }
    createIdea.mutate({ title: ideaForm.title.trim(), source: ideaForm.source.trim() || "manual_note", market: "crypto_perp", symbol_scope: [ideaForm.symbol.trim() || "BTC/USDT"], hypothesis_summary: ideaForm.hypothesis_summary.trim(), rationale: ideaForm.rationale.trim() || null, intake_bucket: "rule_candidate" });
  };

  return (
    <main className="app-shell page-shell strategy-playbook-page">
      <header className="page-header"><p className="eyebrow">Strategy Layer</p><h1>策略库</h1></header>
      {actionMessage ? <div className="action-line">{actionMessage}</div> : null}
      <div className="playbook-tabs" role="tablist" aria-label="策略库视图">
        {TABS.map(([id, label]) => <button key={id} type="button" role="tab" aria-selected={activeTab === id} className={activeTab === id ? "active" : ""} onClick={() => setActiveTab(id)}>{label}</button>)}
      </div>
      {activeTab === "assets" ? <AssetsTab {...{ navigate, strategies, drafts, ideas, materializeDraft, promoteIdea, createIdea, showIdeaForm, setShowIdeaForm, ideaForm, setIdeaForm, submitIdea }} /> : null}
      {activeTab !== "assets" ? <PlaybookTab activeTab={activeTab} query={playbook} updateRoadmap={updateRoadmap} /> : null}
    </main>
  );
}

function Evidence({ metadata }) {
  return <footer className="playbook-evidence">数据来源：{metadata.source_documents.join("、")} · 最后核对：{metadata.verified_on} · commit {metadata.verified_commit.slice(0, 12)}<br />{metadata.disclaimer}</footer>;
}

function PlaybookTab({ activeTab, query, updateRoadmap }) {
  if (query.isLoading) return <section className="exchange-panel playbook-empty">正在读取策略规则…</section>;
  if (query.isError) return <section className="exchange-panel playbook-empty"><p>策略说明加载失败：{query.error.message}</p><button type="button" onClick={() => query.refetch()}>重试</button></section>;
  const data = query.data;
  return <section className="playbook-content" role="tabpanel">
    {activeTab === "overview" ? <Overview data={data} /> : null}
    {activeTab === "entry" ? <EntryLogic data={data} /> : null}
    {activeTab === "exit" ? <ExitLogic data={data} /> : null}
    {activeTab === "position" ? <PositionLogic data={data} /> : null}
    {activeTab === "llm" ? <LlmBoundary data={data} /> : null}
    {activeTab === "sources" ? <Sources data={data} /> : null}
    {activeTab === "roadmap" ? <Roadmap data={data} updateRoadmap={updateRoadmap} /> : null}
    <Evidence metadata={data.metadata} />
  </section>;
}

function Overview({ data }) {
  return <><div className="playbook-heading"><h2>两条研究通道</h2><p>套利与方向策略共享验证、风控、执行和复盘关口。</p></div><div className="table-panel"><table><thead><tr><th>通道</th><th>定位</th><th>核心假设</th><th>成熟度</th></tr></thead><tbody>{data.channels.map((item) => <tr key={item.channel_id}><td><strong>{item.name}</strong></td><td>{item.positioning}</td><td>{item.core_assumption}</td><td>{item.maturity}</td></tr>)}</tbody></table></div></>;
}

function EntryLogic({ data }) {
  return <><div className="playbook-heading"><h2>开单决策链</h2><p>每一步都保留结构化证据，任何关口拒绝即停止。</p></div><ol className="decision-flow">{data.decision_stages.map((stage) => <li key={stage.stage_id}><strong>{stage.name}</strong><span>{stage.description}</span></li>)}</ol><div className="table-panel"><table><thead><tr><th>信号</th><th>参数</th><th>触发条件</th><th>职责</th></tr></thead><tbody>{data.technical_signals.map((signal) => <tr key={signal.signal_id}><td><strong>{signal.name}</strong></td><td>{Object.entries(signal.parameters).map(([key, value]) => `${key}=${value}`).join(" · ")}</td><td>{signal.trigger}</td><td>{signal.role}</td></tr>)}</tbody></table></div></>;
}

function ExitLogic({ data }) {
  return <><div className="playbook-heading"><h2>平单优先级</h2><p>保护退出优先于新信号，反向信号不直接反手。</p></div><div className="rule-list">{data.exit_rules.sort((a, b) => a.priority - b.priority).map((rule) => <article key={rule.rule_id}><span>{rule.priority}</span><div><strong>{rule.name}</strong><p>{rule.description}</p></div></article>)}</div></>;
}

function PositionLogic({ data }) {
  return <><div className="playbook-heading"><h2>风险预算仓位</h2><code className="formula-line">{data.position_sizing.formula}</code></div><div className="table-panel"><table><thead><tr><th>参数</th><th>当前值</th><th>作用域</th><th>代码来源</th></tr></thead><tbody>{data.position_sizing.defaults.map((item) => <tr key={item.key}><td>{item.key}</td><td>{item.value}</td><td>{item.scope}</td><td>{item.source_ref}</td></tr>)}</tbody></table></div><GapList title="尚需加强" items={data.position_sizing.limitations} /></>;
}

function LlmBoundary({ data }) {
  return <><div className="playbook-heading"><h2>LLM 与 RAG 边界</h2><p>LLM只能辅助研究和否决，不能成为订单决策者。</p></div><div className="boundary-grid"><GapList title="允许" items={data.llm_rag.allowed} /><GapList title="禁止" items={data.llm_rag.forbidden} /></div><dl className="playbook-facts"><dt>检索形态</dt><dd>{data.llm_rag.retrieval_mode}</dd><dt>Provider容灾</dt><dd>{data.llm_rag.provider_chain.join(" → ")}</dd></dl><GapList title="当前限制" items={data.llm_rag.limitations} /></>;
}

function Sources({ data }) {
  return <><div className="playbook-heading"><h2>外部策略来源</h2><p>所有来源先转规则、再验证，不直接复制到执行主链。</p></div><div className="table-panel"><table><thead><tr><th>项目</th><th>License</th><th>策略边界</th><th>可吸收内容</th><th>平台映射</th><th>状态</th></tr></thead><tbody>{data.external_sources.map((source) => <tr key={source.source_id}><td><a href={source.repo_url} target="_blank" rel="noreferrer">{source.name}</a></td><td>{source.license}</td><td>{source.license_policy}</td><td>{source.absorbable_content}</td><td>{source.platform_mapping}</td><td>{source.implementation_status}</td></tr>)}</tbody></table></div></>;
}

function Roadmap({ data, updateRoadmap }) {
  return <><div className="playbook-heading"><h2>优化路线图</h2><p>状态持久化并记录更新者；算法改动仍须单独回测验收。</p></div><div className="roadmap-list">{data.roadmap.map((item) => <article key={item.item_id}><span className={`priority ${item.priority.toLowerCase()}`}>{item.priority}</span><div><strong>{item.title}</strong><p>{item.description}</p><small>{item.optimization_target}{item.note ? ` · ${item.note}` : ""}</small></div><select aria-label={`${item.title}状态`} value={item.status} disabled={updateRoadmap.isPending} onChange={(event) => updateRoadmap.mutate({ itemId: item.item_id, status: event.target.value })}><option value="pending">待处理</option><option value="in_progress">进行中</option><option value="done">已完成</option></select></article>)}</div></>;
}

function GapList({ title, items }) { return <section className="plain-list"><h3>{title}</h3><ul>{items.map((item) => <li key={item}>{item}</li>)}</ul></section>; }

function AssetsTab({ navigate, strategies, drafts, ideas, materializeDraft, promoteIdea, createIdea, showIdeaForm, setShowIdeaForm, ideaForm, setIdeaForm, submitIdea }) {
  const strategyRows = asArray(strategies.data?.items); const draftRows = asArray(drafts.data?.items); const ideaRows = asArray(ideas.data?.items);
  return <section className="assets-tab" role="tabpanel"><section className="form-row"><button type="button" onClick={() => setShowIdeaForm((current) => !current)}>{showIdeaForm ? "收起研究想法" : "登记研究想法"}</button></section>{showIdeaForm ? <section className="exchange-panel form-panel"><div className="panel-title"><h2>研究想法登记</h2></div><form className="form-grid" onSubmit={submitIdea}><input aria-label="想法标题" placeholder="想法标题" value={ideaForm.title} onChange={(event) => setIdeaForm((current) => ({ ...current, title: event.target.value }))} /><input aria-label="来源" placeholder="来源" value={ideaForm.source} onChange={(event) => setIdeaForm((current) => ({ ...current, source: event.target.value }))} /><input aria-label="交易标的" placeholder="交易标的" value={ideaForm.symbol} onChange={(event) => setIdeaForm((current) => ({ ...current, symbol: event.target.value }))} /><textarea aria-label="可验证核心假设" placeholder="可验证核心假设" value={ideaForm.hypothesis_summary} onChange={(event) => setIdeaForm((current) => ({ ...current, hypothesis_summary: event.target.value }))} /><textarea aria-label="研究依据" placeholder="研究依据（可选）" value={ideaForm.rationale} onChange={(event) => setIdeaForm((current) => ({ ...current, rationale: event.target.value }))} /><button type="submit" disabled={createIdea.isPending}>{createIdea.isPending ? "登记中" : "登记"}</button></form></section> : null}<section className="records-grid"><StrategyTable title="已物化策略" rows={strategyRows} columns={["strategy_id", "market", "timeframe", "risk_level", "paper_status"]} onRowClick={(row) => row.strategy_id && navigate(`/strategies/${row.strategy_id}`)} /><StrategyTable title="规则草稿" rows={draftRows} columns={["draft_id", "source", "market", "timeframe", "draft_status"]} renderActions={(row) => <button type="button" onClick={() => materializeDraft.mutate(row.draft_id)} disabled={materializeDraft.isPending}>物化</button>} /></section><section className="exchange-panel table-panel"><div className="panel-title"><h2>研究想法</h2><span>{ideaRows.length}</span></div><table><thead><tr><th>ID</th><th>来源</th><th>市场</th><th>状态</th><th>核心假设</th><th>操作</th></tr></thead><tbody>{ideaRows.length ? ideaRows.map((item) => <tr key={item.idea_id}><td>{item.idea_id}</td><td>{item.source}</td><td>{item.market}</td><td>{item.idea_status ?? item.intake_bucket ?? "-"}</td><td>{ideaSummary(item)}</td><td><button type="button" onClick={() => promoteIdea.mutate(item.idea_id)} disabled={promoteIdea.isPending}>晋升草稿</button></td></tr>) : <tr><td colSpan="6">暂无策略想法。先登记一个可验证的研究想法。</td></tr>}</tbody></table></section></section>;
}

function StrategyTable({ title, rows, columns, onRowClick, renderActions }) { return <div className="exchange-panel table-panel"><div className="panel-title"><h2>{title}</h2><span>{rows.length}</span></div><table><thead><tr>{columns.map((column) => <th key={column}>{column}</th>)}{renderActions ? <th>操作</th> : null}</tr></thead><tbody>{rows.length ? rows.map((row, index) => <tr key={row.strategy_id ?? row.draft_id ?? index} className={onRowClick ? "clickable-row" : undefined} onClick={onRowClick ? () => onRowClick(row) : undefined}>{columns.map((column) => <td key={column}>{String(row[column] ?? "-")}</td>)}{renderActions ? <td onClick={(event) => event.stopPropagation()}>{renderActions(row)}</td> : null}</tr>) : <tr><td colSpan={columns.length + (renderActions ? 1 : 0)}>暂无记录</td></tr>}</tbody></table></div>; }
