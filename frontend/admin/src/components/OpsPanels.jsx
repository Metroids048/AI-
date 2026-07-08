import { asArray } from "../utils/format";

export function FeedPanel({ title, items, renderItem }) {
  const rows = asArray(items).slice(0, 8);
  return (
    <section className="exchange-panel feed-panel">
      <div className="panel-title">
        <h2>{title}</h2>
        <span>{rows.length}</span>
      </div>
      <div className="feed-list">
        {rows.length ? (
          rows.map((item, index) => (
            <article key={item.id ?? item.review_report_id ?? item.notification_id ?? `${title}-${index}`}>
              {renderItem(item)}
            </article>
          ))
        ) : (
          <div className="empty-list">暂无数据</div>
        )}
      </div>
    </section>
  );
}
