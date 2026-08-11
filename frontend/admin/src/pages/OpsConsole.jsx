import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { request } from "../api/client";
import { StatusPill } from "../components/Common";
import { FeedPanel } from "../components/OpsPanels";
import { AutoEngineStatusBadge } from "../components/TradingConsolePanels";
import { useRuntimeTruth } from "../hooks/useRuntimeTruth";
import { asArray, formatBoolean, formatEnum, formatFieldLabel, formatTime } from "../utils/format";

export function OpsConsole() {
  const queryClient = useQueryClient();
  const runtime = useRuntimeTruth();
  const health = useQuery({
    queryKey: ["ops-dependency-health"],
    queryFn: () => request("/api/v1/system/health/dependencies"),
    refetchInterval: 15000,
  });
  const agents = useQuery({
    queryKey: ["ops-agent-tasks"],
    queryFn: () => request("/api/v1/agents/tasks"),
    refetchInterval: 10000,
  });
  const news = useQuery({
    queryKey: ["ops-news"],
    queryFn: () => request("/api/v1/market/news?limit=20&refresh=false"),
    refetchInterval: 30000,
  });
  const macro = useQuery({
    queryKey: ["ops-macro-events"],
    queryFn: () => request("/api/v1/market/macro-events?limit=20&refresh=false"),
    refetchInterval: 30000,
  });
  const intelligence = useQuery({
    queryKey: ["ops-market-intelligence"],
    queryFn: () => request("/api/v1/market-intelligence/signals?symbol=BTC/USDT"),
    refetchInterval: 30000,
  });
  const refreshIntelligence = useMutation({
    mutationFn: () => request("/api/v1/market-intelligence/refresh?symbol=BTC/USDT", { method: "POST" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["ops-market-intelligence"] }),
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
  const scheduler = runtime.snapshot?.scheduler;
  const schedulerValue = scheduler?.value ?? {};
  const tradingStatus = {
    scheduler_running: scheduler?.status === "available" && schedulerValue.running === true,
    scheduler_mode: schedulerValue.mode ?? "v2_active",
    scheduler_error: scheduler?.error ?? null,
    next_cycle_eta_seconds: schedulerValue.next_cycle_eta_seconds,
    active_execution_symbols: schedulerValue.active_execution_symbols,
    active_execution_count: schedulerValue.active_execution_count,
    market_data_coverage_count: schedulerValue.market_data_coverage_count,
  };
  const schedulerTone = tradingStatus.scheduler_running ? "ok" : "warn";

  return (
    <main className="app-shell page-shell">
      <header className="page-header">
        <p className="eyebrow">运维 / Agent 层</p>
        <h1>运维控制台</h1>
      </header>

      <AutoEngineStatusBadge status={tradingStatus} />
      {health.isError || scheduler?.status === "unavailable" ? (
        <section className="action-message error" role="alert">
          运维状态接口不可用，当前页面数据不能作为系统正常运行的证据。
        </section>
      ) : null}
      <section className="form-row">
        <button type="button" onClick={() => refreshIntelligence.mutate()} disabled={refreshIntelligence.isPending}>
          {refreshIntelligence.isPending ? "刷新中" : "刷新市场情报"}
        </button>
      </section>

      <section className="status-row ops-status-row">
        <StatusPill label="系统健康" value={health.data?.status === "ok" ? "正常" : health.data?.status ?? "加载中"} tone={health.data?.status === "ok" ? "ok" : "warn"} />
        <StatusPill label="调度器" value={tradingStatus.scheduler_mode} tone={schedulerTone} />
        <StatusPill label="自动循环" value={tradingStatus.scheduler_running ? "运行中" : scheduler?.status === "unavailable" ? "状态待确认" : "已停止"} tone={schedulerTone} />
        <StatusPill label="交易模式" value="币安模拟盘" />
        <StatusPill label="账户数据" value={runtime.snapshot?.exchange?.status === "available" ? "正常" : runtime.snapshot?.exchange?.status === "stale" ? "数据延迟" : "状态待确认"} />
        <StatusPill label="对账" value={runtime.reconciliation?.status ?? "状态待确认"} />
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
                  <td>{formatEnum(item.status)}</td>
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
              {capabilityRows.length ? capabilityRows.map((item, index) => (
                <tr key={`${item.exchange}:${item.gateway_name ?? index}`}>
                  <td>{item.exchange}</td>
                  <td>{formatBoolean(item.supports_market_data)}</td>
                  <td>{formatBoolean(item.supports_order_submit)}</td>
                  <td>{formatBoolean(item.supports_account_sync)}</td>
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
          loading={news.isLoading} error={news.isError ? news.error : null} empty="暂无新闻输入"
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
          loading={macro.isLoading} error={macro.isError ? macro.error : null} empty="暂无宏观事件"
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
          loading={intelligence.isLoading} error={intelligence.isError ? intelligence.error : null} empty="暂无情报源状态"
          renderItem={(item) => (
            <>
              <strong>{item.provider}</strong>
              <span>{formatEnum(item.status)} / {formatEnum(item.configured ? "configured" : "missing")}</span>
            </>
          )}
        />
        <FeedPanel
          title="Agent 任务"
          items={agentRows}
          loading={agents.isLoading} error={agents.isError ? agents.error : null} empty="暂无 Agent 任务"
          renderItem={(item) => (
            <>
              <strong>{item.agent_name ?? item.agent_type ?? item.agent_task_id}</strong>
              <span>{formatEnum(item.task_status)} / {item.task_type ?? item.executor_name ?? "-"}</span>
            </>
          )}
        />
        <FeedPanel
          title="通知出站"
          items={notificationRows}
          loading={notifications.isLoading} error={notifications.isError ? notifications.error : null} empty="暂无通知"
          renderItem={(item) => (
            <>
              <strong>{item.subject}</strong>
              <span>{item.delivery_status} / {item.severity}</span>
            </>
          )}
        />
        <FeedPanel
          title="调度状态"
          items={schedulerRows({ ...tradingStatus, ...schedulerValue, scheduler_error: scheduler?.error })}
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
  const taskRows = Object.entries(status.task_run_counts ?? {}).map(([name, count]) => ({
    name,
    value: `runs ${count} / failures ${status.task_failure_counts?.[name] ?? 0} / last ${formatTime(status.task_last_success_at?.[name])}`,
  }));
  return [
    { name: formatFieldLabel("last_auto_cycle_at"), value: formatTime(status.last_auto_cycle_at) },
    { name: formatFieldLabel("next_cycle_eta_seconds"), value: status.next_cycle_eta_seconds ?? "-" },
    { name: formatFieldLabel("scheduler_error"), value: formatEnum(status.scheduler_error, "无") },
    {
      name: formatFieldLabel("execution_scope_coverage"),
      value: `${status.market_data_coverage_count ?? status.top20_coverage_count ?? 0}/${status.active_execution_count ?? 3}`,
    },
    { name: formatFieldLabel("execution_symbols"), value: (status.active_execution_symbols ?? []).join(", ") || "暂不可用" },
    { name: formatFieldLabel("acceptance_scope"), value: status.acceptance_scope_hash ?? "未验证" },
    { name: formatFieldLabel("last_strategy_gateway_order"), value: status.last_strategy_gateway_order_id ?? "无" },
    { name: formatFieldLabel("queue_backlog"), value: formatEnum(status.queue_backlog_status) },
    ...taskRows,
  ];
}
