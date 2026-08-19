import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { request } from "../api/client";
import { ActionMessage } from "../components/DetailPanels";
import { asArray, formatEnum, formatNumber } from "../utils/format";

function ideaSummary(item) {
  return item.hypothesis_summary ?? item.core_thesis ?? "-";
}

export function ResearchDesk() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [actionMessage, setActionMessage] = useState("");

  const sources = useQuery({
    queryKey: ["research-sources"],
    queryFn: () => request("/api/v1/research-sources"),
    staleTime: 30000,
  });
  const ideas = useQuery({
    queryKey: ["research-strategy-ideas"],
    queryFn: () => request("/api/v1/strategies/ideas"),
    refetchInterval: 15000,
  });
  const news = useQuery({
    queryKey: ["research-news"],
    queryFn: () => request("/api/v1/market/news?limit=20&refresh=false"),
    refetchInterval: 30000,
  });
  const macro = useQuery({
    queryKey: ["research-macro"],
    queryFn: () => request("/api/v1/market/macro-events?limit=20&refresh=false"),
    refetchInterval: 30000,
  });
  const runtime = useQuery({
    queryKey: ["research-runtime"],
    queryFn: () => request("/api/v1/research-sources/runtime"),
    refetchInterval: 15000,
  });

  const promoteIdea = useMutation({
    mutationFn: (ideaId) => request(`/api/v1/strategies/ideas/${ideaId}/drafts`, { method: "POST", body: "{}" }),
    onSuccess: () => {
      setActionMessage("策略想法已晋升为草稿。");
      queryClient.invalidateQueries({ queryKey: ["research-strategy-ideas"] });
      queryClient.invalidateQueries({ queryKey: ["strategy-drafts"] });
    },
    onError: (err) => setActionMessage(`晋升草稿失败：${err.message}`),
  });

  const sourceRows = asArray(sources.data?.items);
  const ideaRows = asArray(ideas.data?.items);
  const newsRows = asArray(news.data?.items);
  const macroRows = asArray(macro.data?.items);
  const engineRows = Object.values(runtime.data?.engines ?? {});
  const runSummary = runtime.data?.runs ?? {};
  const researchTruth = runtime.data?.research_truth ?? {};
  const assetCount = sourceRows.reduce((total, item) => total + Number(item.asset_count ?? item.local_asset_count ?? 0), 0);

  return (
    <main className="app-shell page-shell">
      <header className="page-header">
        <p className="eyebrow">数据 / 研究来源</p>
        <h1>研究工作台</h1>
      </header>
      <ActionMessage message={actionMessage} />
      {sources.isError ? <div className="action-line">研究源加载失败：{sources.error.message}</div> : null}
      <section className="form-row">
        <button type="button" onClick={() => { news.refetch(); macro.refetch(); }} disabled={news.isFetching || macro.isFetching}>
          {news.isFetching || macro.isFetching ? "刷新中" : "刷新新闻与宏观数据"}
        </button>
      </section>

      <section className="funding-metrics">
        <div className="metric"><span>开源研究源</span><strong>{sources.isLoading ? "加载中" : sourceRows.length}</strong></div>
        <div className="metric"><span>本地资产</span><strong>{formatNumber(assetCount, 0)}</strong></div>
        <div className="metric"><span>策略想法</span><strong>{ideas.isLoading ? "加载中" : ideaRows.length}</strong></div>
        <div className="metric"><span>新闻 / 宏观输入</span><strong>{news.isLoading || macro.isLoading ? "加载中" : newsRows.length + macroRows.length}</strong></div>
        <div className="metric"><span>研究队列</span><strong>{runtime.isLoading ? "加载中" : Number(runSummary.queued ?? 0) + Number(runSummary.running ?? 0)}</strong></div>
        <div className="metric"><span>生产授权</span><strong>{runtime.data?.production_authorization ?? "未知"}</strong></div>
      </section>

      <section className="records-grid">
        <section className="exchange-panel table-panel">
          <div className="panel-title"><h2>研究引擎与来源 Pin</h2><span>{engineRows.length}</span></div>
          {runtime.isError ? <div className="empty-list">研究运行时不可用：{runtime.error.message}</div> : (
            <table>
              <thead><tr><th>引擎</th><th>可用性</th><th>SHA</th><th>模式</th></tr></thead>
              <tbody>{engineRows.map((item) => (
                <tr key={item.engine}><td>{item.engine}</td><td>{item.available ? "available" : "unavailable"}</td><td>{item.sha}</td><td>{item.mode}</td></tr>
              ))}</tbody>
            </table>
          )}
        </section>
        <section className="exchange-panel table-panel">
          <div className="panel-title"><h2>实验状态</h2><span>{Number(runSummary.completed ?? 0) + Number(runSummary.failed ?? 0)}</span></div>
          <table>
            <thead><tr><th>类型</th><th>引擎</th><th>状态</th><th>Run</th></tr></thead>
            <tbody>{asArray(runSummary.recent).length ? asArray(runSummary.recent).map((item) => (
              <tr key={`${item.type}-${item.id}`}><td>{item.type}</td><td>{item.engine}</td><td>{item.status}</td><td>{item.id}</td></tr>
            )) : <tr><td colSpan="4">暂无外部研究运行</td></tr>}</tbody>
          </table>
        </section>
      </section>

      <section className="records-grid">
        <ResearchEvidenceTable
          title="Top Candidates / Plateau"
          count={asArray(researchTruth.top_candidates).length}
          headers={["Run", "参数", "净期望", "PF"]}
          rows={asArray(researchTruth.top_candidates).map((item) => [
            item.run_id,
            JSON.stringify(item.parameters ?? {}),
            formatNumber(item.expectancy_net_r, 4),
            formatNumber(item.profit_factor, 3),
          ])}
          empty="暂无可追溯的候选参数高原"
        />
        <ResearchEvidenceTable
          title="Bias Gate / Native OOS"
          count={asArray(researchTruth.bias_gates).length}
          headers={["Run", "Lookahead", "Recursive", "Native OOS"]}
          rows={asArray(researchTruth.bias_gates).map((item, index) => [
            item.run_id,
            item.lookahead,
            item.recursive,
            asArray(researchTruth.native_oos)[index]?.status ?? "NOT_RUN",
          ])}
          empty="暂无 bias 或 native OOS 证据"
        />
        <ResearchEvidenceTable
          title="Council Verdict"
          count={asArray(researchTruth.council).length}
          headers={["Run", "Verdict"]}
          rows={asArray(researchTruth.council).map((item) => [item.run_id, item.verdict])}
          empty="暂无研究委员会结论"
        />
      </section>

      <section className="records-grid">
        <section className="exchange-panel table-panel">
          <div className="panel-title"><h2>GitHub / 开源策略源</h2><span>{sourceRows.length}</span></div>
          <table>
            <thead>
              <tr>
                <th>Source</th>
                <th>类型</th>
                <th>许可证</th>
                <th>Pin</th>
                <th>融合模式</th>
                <th>资产</th>
                <th>状态</th>
              </tr>
            </thead>
            <tbody>
              {sourceRows.length ? sourceRows.map((item) => (
                <tr
                  key={item.source_id}
                  className="clickable-row"
                  onClick={() => navigate(`/research/sources/${item.source_id}`)}
                >
                  <td>{item.source_id}</td>
                  <td>{item.source_type ?? item.category ?? "-"}</td>
                  <td>{item.license ?? item.license_policy ?? "-"}</td>
                  <td>{item.upstream_sha ? item.upstream_sha.slice(0, 12) : "-"}</td>
                  <td>{item.integration_mode ?? "research_reference"}</td>
                  <td>{item.asset_count ?? item.local_asset_count ?? 0}</td>
                  <td>{formatEnum(item.ingestion_status ?? item.status, "已登记")}</td>
                </tr>
              )) : <tr><td colSpan="7">暂无研究源</td></tr>}
            </tbody>
          </table>
        </section>

        <section className="exchange-panel table-panel">
          <div className="panel-title"><h2>策略想法</h2><span>{ideaRows.length}</span></div>
          <table>
            <thead>
              <tr>
                <th>ID</th>
                <th>来源</th>
                <th>市场</th>
                <th>状态</th>
                <th>假设</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {ideaRows.length ? ideaRows.slice(0, 12).map((item) => (
                <tr key={item.idea_id}>
                  <td>{item.idea_id}</td>
                  <td>{item.source}</td>
                  <td>{item.market}</td>
                  <td>{formatEnum(item.idea_status ?? item.intake_bucket, "-")}</td>
                  <td>{ideaSummary(item)}</td>
                  <td>
                    <button
                      type="button"
                      onClick={() => promoteIdea.mutate(item.idea_id)}
                      disabled={promoteIdea.isPending}
                    >
                      晋升草稿
                    </button>
                  </td>
                </tr>
              )) : <tr><td colSpan="6">暂无策略想法</td></tr>}
            </tbody>
          </table>
        </section>
      </section>

      <section className="ops-grid">
        <FeedPanel title="新闻研究输入" rows={newsRows} loading={news.isLoading} error={news.isError ? news.error : null} empty="暂无新闻输入" render={(item) => (
          <>
            <strong>{item.title}</strong>
            <span>{item.source ?? "rss"} / {item.severity ?? item.relevance_status ?? "captured"}</span>
          </>
        )} />
        <FeedPanel title="宏观研究输入" rows={macroRows} loading={macro.isLoading} error={macro.isError ? macro.error : null} empty="暂无宏观事件" render={(item) => (
          <>
            <strong>{item.event_name}</strong>
            <span>{item.impact ?? "-"} / {item.country ?? item.source ?? "-"}</span>
          </>
        )} />
      </section>
    </main>
  );
}

function ResearchEvidenceTable({ title, count, headers, rows, empty }) {
  return (
    <section className="exchange-panel table-panel">
      <div className="panel-title"><h2>{title}</h2><span>{count}</span></div>
      <table>
        <thead><tr>{headers.map((header) => <th key={header}>{header}</th>)}</tr></thead>
        <tbody>{rows.length ? rows.map((row, index) => (
          <tr key={`${title}-${index}`}>{row.map((value, cellIndex) => <td key={`${index}-${cellIndex}`}>{value}</td>)}</tr>
        )) : <tr><td colSpan={headers.length}>{empty}</td></tr>}</tbody>
      </table>
    </section>
  );
}

function FeedPanel({ title, rows, empty, render, loading = false, error = null }) {
  return (
    <section className="exchange-panel feed-panel">
      <div className="panel-title"><h2>{title}</h2><span>{rows.length}</span></div>
      <div className="feed-list">
        {loading ? <div className="empty-list">正在加载…</div> : error ? <div className="empty-list">加载失败：{error.message ?? "服务暂不可用"}</div> : rows.length ? rows.slice(0, 8).map((item, index) => (
          <article key={item.id ?? item.event_id ?? index}>{render(item)}</article>
        )) : <div className="empty-list">{empty}</div>}
      </div>
    </section>
  );
}
