import { useQuery } from "@tanstack/react-query";

import { request } from "../api/client";
import { FeedPanel } from "../components/OpsPanels";
import { AutoEngineStatusBadge } from "../components/TradingConsolePanels";
import { formatTime } from "../utils/format";

export function OpsConsole() {
  const status = useQuery({
    queryKey: ["ops-trading-status"],
    queryFn: () => request("/api/v1/execution/trading-status"),
    refetchInterval: 15000,
  });
  const news = useQuery({
    queryKey: ["ops-news"],
    queryFn: () => request("/api/v1/market/news?limit=20"),
    refetchInterval: 30000,
  });
  const macro = useQuery({
    queryKey: ["ops-macro-events"],
    queryFn: () => request("/api/v1/market/macro-events?limit=20"),
    refetchInterval: 30000,
  });
  const notifications = useQuery({
    queryKey: ["ops-notifications"],
    queryFn: () => request("/api/v1/notifications/outbox?limit=20"),
    refetchInterval: 15000,
  });

  return (
    <main className="app-shell page-shell">
      <header className="page-header">
        <p className="eyebrow">Ops / Agent Layer</p>
        <h1>运维控制台</h1>
      </header>
      <AutoEngineStatusBadge status={status.data} />
      <section className="ops-grid">
        <FeedPanel
          title="新闻 / 消息面"
          items={news.data?.items}
          renderItem={(item) => (
            <>
              <strong>{item.title}</strong>
              <span>{item.source} / {item.severity ?? item.relevance_status ?? "captured"}</span>
            </>
          )}
        />
        <FeedPanel
          title="宏观事件窗口"
          items={macro.data?.items}
          renderItem={(item) => (
            <>
              <strong>{item.event_name}</strong>
              <span>{item.impact} / {formatTime(item.scheduled_at)}</span>
            </>
          )}
        />
        <FeedPanel
          title="通知出站"
          items={notifications.data?.items}
          renderItem={(item) => (
            <>
              <strong>{item.subject}</strong>
              <span>{item.delivery_status} / {item.severity}</span>
            </>
          )}
        />
      </section>
    </main>
  );
}
