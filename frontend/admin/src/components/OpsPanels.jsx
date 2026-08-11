import { asArray } from "../utils/format";

export function FeedPanel({ title, items, renderItem, loading = false, error = null, empty = "暂无数据" }) {
  const rows = asArray(items).slice(0, 8);
  return (
    <section className="exchange-panel feed-panel">
      <div className="panel-title">
        <h2>{title}</h2>
        <span>{rows.length}</span>
      </div>
      <div className="feed-list">
        {loading ? <div className="empty-list">正在加载…</div> : error ? <div className="empty-list">加载失败：{error.message ?? "服务暂不可用"}</div> : rows.length ? (
          rows.map((item, index) => (
            <article key={item.id ?? item.review_report_id ?? item.notification_id ?? `${title}-${index}`}>
              {renderItem(item)}
            </article>
          ))
        ) : (
          <div className="empty-list">{empty}</div>
        )}
      </div>
    </section>
  );
}
