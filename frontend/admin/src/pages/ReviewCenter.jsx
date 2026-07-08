import { useQuery } from "@tanstack/react-query";

import { request } from "../api/client";
import { asArray, formatTime } from "../utils/format";

export function ReviewCenter() {
  const reviews = useQuery({
    queryKey: ["review-reports"],
    queryFn: () => request("/api/v1/reviews"),
    refetchInterval: 15000,
  });
  const failures = useQuery({
    queryKey: ["review-failures"],
    queryFn: () => request("/api/v1/failures"),
    refetchInterval: 15000,
  });
  const decisions = useQuery({
    queryKey: ["review-decision-memory"],
    queryFn: () => request("/api/v1/decision-memory"),
    refetchInterval: 15000,
  });
  const news = useQuery({
    queryKey: ["review-news"],
    queryFn: () => request("/api/v1/market/news?limit=12&refresh=true"),
    refetchInterval: 30000,
  });

  const reviewRows = asArray(reviews.data?.items);
  const failureRows = asArray(failures.data?.items);
  const decisionRows = asArray(decisions.data?.items);
  const newsRows = asArray(news.data?.items);

  return (
    <main className="app-shell page-shell">
      <header className="page-header">
        <p className="eyebrow">Review Layer</p>
        <h1>复盘中心</h1>
      </header>

      <section className="ops-grid">
        <FeedPanel
          title="每日复盘"
          rows={reviewRows}
          empty="暂无复盘报告"
          render={(item) => (
            <>
              <strong>{item.report_date ?? item.review_report_id}</strong>
              <span>{item.report_status ?? "-"} / {asArray(item.failure_patterns).length} failure patterns</span>
            </>
          )}
        />
        <FeedPanel
          title="失败知识"
          rows={failureRows}
          empty="暂无失败记录"
          render={(item) => (
            <>
              <strong>{item.failure_type ?? item.failure_record_id}</strong>
              <span>{item.strategy_id ?? item.idea_id ?? "-"} / {item.severity ?? "-"}</span>
            </>
          )}
        />
        <FeedPanel
          title="决策记忆"
          rows={decisionRows}
          empty="暂无决策记忆"
          render={(item) => (
            <>
              <strong>{item.decision_type ?? item.memory_id}</strong>
              <span>{item.verdict ?? "-"} / {item.scope_type ?? "-"}:{item.scope_id ?? "-"}</span>
            </>
          )}
        />
        <FeedPanel
          title="消息面复盘输入"
          rows={newsRows}
          empty="暂无第三方新闻输入"
          render={(item) => (
            <>
              <strong>{item.title}</strong>
              <span>{item.source ?? "rss"} / {item.severity ?? item.relevance_status ?? "captured"}</span>
            </>
          )}
        />
      </section>
    </main>
  );
}

function FeedPanel({ title, rows, empty, render }) {
  return (
    <section className="exchange-panel feed-panel">
      <div className="panel-title"><h2>{title}</h2><span>{rows.length}</span></div>
      <div className="feed-list">
        {rows.length ? rows.slice(0, 10).map((item, index) => (
          <article key={item.review_report_id ?? item.failure_record_id ?? item.memory_id ?? item.id ?? index}>
            {render(item)}
            {item.created_at || item.occurred_at ? <span>{formatTime(item.created_at ?? item.occurred_at)}</span> : null}
          </article>
        )) : <div className="empty-list">{empty}</div>}
      </div>
    </section>
  );
}
