import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { request } from "../api/client";
import { useRuntimeTruth } from "../hooks/useRuntimeTruth";
import { asArray, formatEnum, formatTime } from "../utils/format";

export function ReviewCenter() {
  const queryClient = useQueryClient();
  const [actionMessage, setActionMessage] = useState("");
  const runtime = useRuntimeTruth();
  const reviews = useQuery({
    queryKey: ["review-reports"],
    queryFn: () => request("/api/v1/reviews"),
    refetchInterval: 15000,
  });
  const failures = useQuery({
    queryKey: ["review-failures"],
    queryFn: () => request("/api/v1/failures?limit=50"),
    refetchInterval: 15000,
  });
  const decisions = useQuery({
    queryKey: ["review-decision-memory"],
    queryFn: () => request("/api/v1/decision-memory"),
    refetchInterval: 15000,
  });
  const news = useQuery({
    queryKey: ["review-news"],
    queryFn: () => request("/api/v1/market/news?limit=12&refresh=false"),
    refetchInterval: 30000,
  });
  const intelligence = useQuery({
    queryKey: ["review-market-intelligence"],
    queryFn: () => request("/api/v1/market-intelligence/signals?symbol=BTC/USDT"),
    refetchInterval: 30000,
  });

  const reviewRows = asArray(reviews.data?.items);
  const failureRows = asArray(failures.data?.items);
  const decisionRows = asArray(decisions.data?.items);
  const newsRows = asArray(news.data?.items);
  const runtimeLoading = runtime.snapshot == null;
  const runtimeError = runtime.error || (runtime.snapshot?.exchange?.status === "unavailable" ? runtime.snapshot.exchange.error : "");

  const generateTodayReview = async () => {
    const today = new Date().toISOString().slice(0, 10);
    try {
      await request(`/api/v1/reviews/daily/${today}`, { method: "POST", body: "{}" });
      setActionMessage("今日复盘已生成，失败模式与建议已回写。" );
      await queryClient.invalidateQueries({ queryKey: ["review-reports"] });
    } catch (err) {
      setActionMessage(`生成复盘失败：${err.message}`);
    }
  };

  return (
    <main className="app-shell page-shell">
      <header className="page-header">
        <p className="eyebrow">复盘层</p>
        <h1>复盘中心</h1>
      </header>
      <section className="form-row">
        <button type="button" onClick={generateTodayReview}>生成今日复盘</button>
        <button type="button" onClick={() => news.refetch()} disabled={news.isFetching}>刷新消息面</button>
        {actionMessage ? <span className="action-line">{actionMessage}</span> : null}
      </section>

      <section className="records-grid review-runtime-activity">
        <FeedPanel
          title="当前自动交易决策"
          rows={runtime.decisions}
          loading={runtimeLoading} error={runtimeError ? new Error(runtimeError) : null}
          empty={runtime.snapshot?.exchange?.status === "unavailable" ? "运行事实暂不可用" : "当前没有自动交易决策"}
          render={(item) => (
            <>
              <strong>{item.symbol ?? "未知标的"} · {formatEnum(item.action ?? item.decision, "决策")}</strong>
              <span>{formatEnum(item.terminal_reason ?? item.reason, "已记录")}</span>
            </>
          )}
        />
        <FeedPanel
          title="当前交易所订单"
          rows={runtime.exchangeOrders}
          loading={runtimeLoading} error={runtimeError ? new Error(runtimeError) : null}
          empty={runtime.snapshot?.exchange?.status === "unavailable" ? "交易所订单暂不可用" : "当前没有交易所订单"}
          render={(item) => (
            <>
              <strong>{item.symbol ?? "未知标的"} · {item.side ?? "订单"}</strong>
              <span>{formatEnum(item.state ?? item.status, "已记录")} / {item.exchange_order_id ?? item.order_id ?? "-"}</span>
            </>
          )}
        />
        <FeedPanel
          title="当前对账状态"
          rows={runtime.reconciliation ? [runtime.reconciliation] : []}
          loading={runtimeLoading} error={runtimeError ? new Error(runtimeError) : null}
          empty="对账状态待确认"
          render={(item) => <><strong>{formatEnum(item.status, "待确认")}</strong><span>{asArray(item.entry_blocked_symbols).length ? "存在限制开仓标的" : "暂无阻断标的"}</span></>}
        />
      </section>

      <section className="ops-grid">
        <FeedPanel
          title="每日复盘"
          rows={reviewRows}
          loading={reviews.isLoading} error={reviews.isError ? reviews.error : null}
          empty="暂无复盘报告"
          render={(item) => (
            <>
              <strong>{item.report_date ?? item.review_report_id}</strong>
              <span>{formatEnum(item.report_status, "-")} / {asArray(item.failure_patterns).length} 个失败模式</span>
            </>
          )}
        />
        <FeedPanel
          title="失败知识"
          rows={failureRows}
          loading={failures.isLoading} error={failures.isError ? failures.error : null}
          empty="暂无失败记录"
          render={(item) => (
            <>
              <strong>{formatEnum(item.failure_type, item.failure_record_id)}</strong>
              <span>{item.strategy_id ?? item.idea_id ?? "-"} / {formatEnum(item.severity, "-")}</span>
            </>
          )}
        />
        <FeedPanel
          title="历史决策记忆"
          rows={decisionRows}
          loading={decisions.isLoading} error={decisions.isError ? decisions.error : null}
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
          loading={news.isLoading} error={news.isError ? news.error : null}
          empty="暂无第三方新闻输入"
          render={(item) => (
            <>
              <strong>{item.title}</strong>
              <span>{item.source ?? "rss"} / {formatEnum(item.severity ?? item.relevance_status, "已采集")}</span>
            </>
          )}
        />
        <FeedPanel
          title="情报因子复盘"
          rows={intelligence.data ? [intelligence.data] : []}
          loading={intelligence.isLoading} error={intelligence.isError ? intelligence.error : null}
          empty="暂无情报因子记录"
          render={(item) => (
            <>
              <strong>{item.direction === "long" ? "多" : item.direction === "short" ? "空" : item.direction === "neutral" ? "中性" : item.direction ?? "中性"} / 权重 {item.vote_weight}</strong>
              <span>{item.active_event_cooldown ? "事件冷却中" : item.should_participate ? "投票参与中" : "观察中"}</span>
            </>
          )}
        />
      </section>
    </main>
  );
}

function FeedPanel({ title, rows, empty, render, loading = false, error = null }) {
  return (
    <section className="exchange-panel feed-panel">
      <div className="panel-title"><h2>{title}</h2><span>{rows.length}</span></div>
      <div className="feed-list">
        {loading ? <div className="empty-list">正在加载…</div> : error ? <div className="empty-list">加载失败：{error.message ?? "服务暂不可用"}</div> : rows.length ? rows.slice(0, 10).map((item, index) => (
          <article key={item.review_report_id ?? item.failure_record_id ?? item.memory_id ?? item.id ?? index}>
            {render(item)}
            {item.created_at || item.occurred_at ? <span>{formatTime(item.created_at ?? item.occurred_at)}</span> : null}
          </article>
        )) : <div className="empty-list">{empty}</div>}
      </div>
    </section>
  );
}
