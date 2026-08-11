import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { request } from "../api/client";
import { ActionMessage } from "../components/DetailPanels";
import { asArray, formatNumber } from "../utils/format";

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
                  <td>{item.asset_count ?? item.local_asset_count ?? 0}</td>
                  <td>{item.ingestion_status ?? item.status ?? "registered"}</td>
                </tr>
              )) : <tr><td colSpan="5">暂无研究源</td></tr>}
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
                  <td>{item.idea_status ?? item.intake_bucket ?? "-"}</td>
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
        <FeedPanel title="新闻研究输入" rows={newsRows} empty="暂无新闻输入" render={(item) => (
          <>
            <strong>{item.title}</strong>
            <span>{item.source ?? "rss"} / {item.severity ?? item.relevance_status ?? "captured"}</span>
          </>
        )} />
        <FeedPanel title="宏观研究输入" rows={macroRows} empty="暂无宏观事件" render={(item) => (
          <>
            <strong>{item.event_name}</strong>
            <span>{item.impact ?? "-"} / {item.country ?? item.source ?? "-"}</span>
          </>
        )} />
      </section>
    </main>
  );
}

function FeedPanel({ title, rows, empty, render }) {
  return (
    <section className="exchange-panel feed-panel">
      <div className="panel-title"><h2>{title}</h2><span>{rows.length}</span></div>
      <div className="feed-list">
        {rows.length ? rows.slice(0, 8).map((item, index) => (
          <article key={item.id ?? item.event_id ?? index}>{render(item)}</article>
        )) : <div className="empty-list">{empty}</div>}
      </div>
    </section>
  );
}
