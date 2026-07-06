import { asArray, formatTime } from "../utils/format";

export function OpsReviewPanel({ newsItems, macroEvents, reviews, notifications }) {
  return (
    <section className="ops-grid">
      <FeedPanel title="新闻/消息面" items={newsItems} renderItem={(item) => (
        <>
          <strong>{item.title}</strong>
          <span>{item.source} · {item.severity ?? item.relevance_status ?? "captured"}</span>
        </>
      )} />
      <FeedPanel title="宏观事件窗口" items={macroEvents} renderItem={(item) => (
        <>
          <strong>{item.event_name}</strong>
          <span>{item.impact} · {formatTime(item.scheduled_at)}</span>
        </>
      )} />
      <FeedPanel title="复盘报告" items={reviews} renderItem={(item) => (
        <>
          <strong>{item.report_date}</strong>
          <span>{item.report_status} · {asArray(item.failure_patterns).length} failures</span>
        </>
      )} />
      <FeedPanel title="通知出站" items={notifications} renderItem={(item) => (
        <>
          <strong>{item.subject}</strong>
          <span>{item.delivery_status} · {item.severity}</span>
        </>
      )} />
    </section>
  );
}

function FeedPanel({ title, items, renderItem }) {
  const rows = asArray(items).slice(0, 8);
  return (
    <section className="panel feed-panel">
      <div className="panel-heading">
        <h2>{title}</h2>
        <span>{rows.length}</span>
      </div>
      <div className="feed-list">
        {rows.length ? rows.map((item, index) => (
          <article key={item.id ?? item.review_report_id ?? item.notification_id ?? `${title}-${index}`}>
            {renderItem(item)}
          </article>
        )) : <div className="empty-list">暂无数据</div>}
      </div>
    </section>
  );
}
