function formatTime(value) {
  if (!value) return "无成功时间";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
}

function DatumMeta({ datum }) {
  if (!datum) return <span className="truth-meta unavailable">数据不可用</span>;
  return (
    <span className={`truth-meta ${datum.status ?? "unavailable"}`}>
      来源：{datum.source || "未接通"} · 时间：{formatTime(datum.observed_at)} · 新鲜度：
      {datum.freshness || "未知"}
    </span>
  );
}

export function RuntimeTruthPanel({ runtime, symbol }) {
  const snapshot = runtime.snapshot;
  const mismatch = snapshot?.mismatch;
  const decisions = Array.isArray(runtime.decisions) ? runtime.decisions : [];
  const terminal = decisions.find((item) => item.symbol === symbol) ?? decisions[0];
  const exchange = snapshot?.exchange;
  const scheduler = snapshot?.scheduler;
  const mismatchActive = mismatch?.status === "available" && mismatch?.value?.consistent === false;
  const unavailable = exchange?.status !== "available";

  return (
    <section className="runtime-truth-panel" aria-labelledby="runtime-truth-title">
      <div className="runtime-truth-heading">
        <div>
          <p className="eyebrow">Runtime Truth</p>
          <h2 id="runtime-truth-title">为什么没有交易</h2>
        </div>
        <span className={`truth-stream ${runtime.streamStatus}`}>{runtime.streamStatus}</span>
      </div>

      {mismatchActive ? (
        <div className="truth-blocker" role="alert">
          Binance 与本地投影不一致，新 Entry 已阻断。
        </div>
      ) : null}
      {unavailable ? (
        <div className="truth-unavailable" role="status">
          交易所数据不可用：{exchange?.error || runtime.error || "未接通"}。不会用 0 或旧值代替。
        </div>
      ) : null}

      <div className="runtime-truth-grid">
        <article>
          <h3>{symbol}</h3>
          {terminal ? (
            <>
              <strong>{terminal.status === "PASSED" ? "本根 K 线已通过" : "本根 K 线未开仓"}</strong>
              <p>终止阶段：{terminal.terminal_stage}</p>
              <p>原因：{terminal.reason_code}</p>
              <small>决策 K 线：{formatTime(terminal.bar_time)}</small>
            </>
          ) : (
            <p className="empty-copy">暂无闭合 K 线决策终态，不能推断为“无信号”。</p>
          )}
        </article>
        <article>
          <h3>Binance Testnet</h3>
          <strong>{exchange?.status === "available" ? "已接通" : "未接通"}</strong>
          <DatumMeta datum={exchange} />
        </article>
        <article>
          <h3>Scheduler</h3>
          <strong>{scheduler?.status === "available" ? "运行中" : "离线"}</strong>
          <p>{scheduler?.error || "心跳正常"}</p>
          <DatumMeta datum={scheduler} />
        </article>
        <article>
          <h3>AI 调用</h3>
          {runtime.llmInvocations?.[0] ? (
            <>
              <strong>{runtime.llmInvocations[0].called ? "已调用" : "已跳过"}</strong>
              <p>
                {runtime.llmInvocations[0].provider || "无 Provider"} /{" "}
                {runtime.llmInvocations[0].model || runtime.llmInvocations[0].skip_reason}
              </p>
              <small>Tokens：{runtime.llmInvocations[0].total_tokens}</small>
            </>
          ) : (
            <p className="empty-copy">暂无 AI 调用或跳过记录。</p>
          )}
        </article>
      </div>
      <p className="truth-last-success">最后成功更新时间：{formatTime(runtime.lastSuccessAt)}</p>
    </section>
  );
}
