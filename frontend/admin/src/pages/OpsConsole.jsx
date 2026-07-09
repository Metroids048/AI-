import { useQuery } from "@tanstack/react-query";

import { request } from "../api/client";
import { StatusPill } from "../components/Common";
import { FeedPanel } from "../components/OpsPanels";
import { AutoEngineStatusBadge } from "../components/TradingConsolePanels";
import { asArray, formatTime } from "../utils/format";

export function OpsConsole() {
  const health = useQuery({
    queryKey: ["ops-dependency-health"],
    queryFn: () => request("/api/v1/system/health/dependencies"),
    refetchInterval: 15000,
  });
  const tradingStatus = useQuery({
    queryKey: ["ops-trading-status"],
    queryFn: () => request("/api/v1/execution/trading-status"),
    refetchInterval: 5000,
  });
  const agents = useQuery({
    queryKey: ["ops-agent-tasks"],
    queryFn: () => request("/api/v1/agents/tasks"),
    refetchInterval: 10000,
  });
  const news = useQuery({
    queryKey: ["ops-news"],
    queryFn: () => request("/api/v1/market/news?limit=20&refresh=true"),
    refetchInterval: 30000,
  });
  const macro = useQuery({
    queryKey: ["ops-macro-events"],
    queryFn: () => request("/api/v1/market/macro-events?limit=20&refresh=true"),
    refetchInterval: 30000,
  });
  const intelligence = useQuery({
    queryKey: ["ops-market-intelligence"],
    queryFn: () => request("/api/v1/market-intelligence/refresh?symbol=BTC/USDT"),
    refetchInterval: 30000,
  });
  const notifications = useQuery({
    queryKey: ["ops-notifications"],
    queryFn: () => request("/api/v1/notifications/outbox?limit=20"),
    refetchInterval: 10000,
  });
  const capabilities = useQuery({
    queryKey: ["ops-market-capabilities"],
    queryFn: () => request("/api/v1/market/capabilities"),
    staleTime: 30000,
  });

  const dependencyRows = Object.entries(health.data?.dependencies ?? {});
  const agentRows = asArray(agents.data?.items);
  const notificationRows = asArray(notifications.data?.items);
  const capabilityRows = asArray(capabilities.data?.items);
  const providerRows = Object.values(intelligence.data?.provider_status ?? {});
  const schedulerTone = tradingStatus.data?.scheduler_running ? "ok" : "warn";

  return (
    <main className="app-shell page-shell">
      <header className="page-header">
        <p className="eyebrow">Ops / Agent Layer</p>
        <h1>运维控制台</h1>
      </header>

      <AutoEngineStatusBadge status={tradingStatus.data} />

      <section className="status-row ops-status-row">
        <StatusPill label="系统健康" value={health.data?.status ?? "loading"} tone={health.data?.status === "ok" ? "ok" : "warn"} />
        <StatusPill label="调度器" value={tradingStatus.data?.scheduler_mode ?? "unknown"} tone={schedulerTone} />
        <StatusPill label="自动循环" value={tradingStatus.data?.scheduler_running ? "running" : "stopped"} tone={schedulerTone} />
        <StatusPill label="交易模式" value={tradingStatus.data?.mode ?? "paper"} />
        <StatusPill label="凭据" value={tradingStatus.data?.credentials_configured ? "configured" : "missing"} />
        <StatusPill label="Live Feed" value={Object.keys(tradingStatus.data?.live_feed_status ?? {}).length} />
      </section>

      <section className="records-grid">
        <section className="exchange-panel table-panel">
          <div className="panel-title"><h2>依赖健康</h2><span>{dependencyRows.length}</span></div>
          <table>
            <thead>
              <tr>
                <th>依赖</th>
                <th>状态</th>
                <th>详情</th>
              </tr>
            </thead>
            <tbody>
              {dependencyRows.length ? dependencyRows.map(([name, item]) => (
                <tr key={name}>
                  <td>{name}</td>
                  <td>{item.status}</td>
                  <td>{item.detail}</td>
                </tr>
              )) : <tr><td colSpan="3">依赖检查加载中</td></tr>}
            </tbody>
          </table>
        </section>

        <section className="exchange-panel table-panel">
          <div className="panel-title"><h2>第三方能力</h2><span>{capabilityRows.length}</span></div>
          <table>
            <thead>
              <tr>
                <th>交易所</th>
                <th>市场数据</th>
                <th>下单</th>
                <th>账户同步</th>
              </tr>
            </thead>
            <tbody>
              {capabilityRows.length ? capabilityRows.map((item) => (
                <tr key={item.exchange}>
                  <td>{item.exchange}</td>
                  <td>{String(item.supports_market_data)}</td>
                  <td>{String(item.supports_order_submit)}</td>
                  <td>{String(item.supports_account_sync)}</td>
                </tr>
              )) : <tr><td colSpan="4">暂无能力记录</td></tr>}
            </tbody>
          </table>
        </section>
      </section>

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
          title="情报源状态"
          items={providerRows}
          renderItem={(item) => (
            <>
              <strong>{item.provider}</strong>
              <span>{item.status} / {item.configured ? "configured" : "missing"}</span>
            </>
          )}
        />
        <FeedPanel
          title="Agent 任务"
          items={agentRows}
          renderItem={(item) => (
            <>
              <strong>{item.agent_name ?? item.agent_type ?? item.agent_task_id}</strong>
              <span>{item.task_status ?? "-"} / {item.task_type ?? item.executor_name ?? "-"}</span>
            </>
          )}
        />
        <FeedPanel
          title="通知出站"
          items={notificationRows}
          renderItem={(item) => (
            <>
              <strong>{item.subject}</strong>
              <span>{item.delivery_status} / {item.severity}</span>
            </>
          )}
        />
        <FeedPanel
          title="调度状态"
          items={schedulerRows(tradingStatus.data)}
          renderItem={(item) => (
            <>
              <strong>{item.name}</strong>
              <span>{item.value}</span>
            </>
          )}
        />
      </section>
    </main>
  );
}

function schedulerRows(status) {
  if (!status) return [];
  return [
    { name: "last_auto_cycle_at", value: formatTime(status.last_auto_cycle_at) },
    { name: "next_cycle_eta_seconds", value: status.next_cycle_eta_seconds ?? "-" },
    { name: "scheduler_error", value: status.scheduler_error ?? "none" },
  ];
}
