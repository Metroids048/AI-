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

function listCount(items) {
  return Array.isArray(items) ? items.length : 0;
}

function entryPauseExplanation(reason) {
  if (reason === "NO_AUTHORIZED_PRODUCTION_STRATEGY" || reason === "production_pending") {
    return "暂无通过验证的生产策略：策略尚未通过收益/风险验证，不是系统故障。";
  }
  return `原因：${reason || "NO_ENTRY_AUTHORITY"}`;
}

export function RuntimeTruthPanel({ runtime, symbol }) {
  const snapshot = runtime.snapshot;
  const mismatch = snapshot?.mismatch;
  const decisions = Array.isArray(runtime.decisions) ? runtime.decisions : [];
  const terminal = decisions.find((item) => item.symbol === symbol) ?? decisions[0];
  const exchange = snapshot?.exchange;
  const localProjection = snapshot?.local_projection;
  const scheduler = snapshot?.scheduler;
  const entryRuntime = snapshot?.entry_runtime;
  const entryRuntimeValue = entryRuntime?.value;
  const strategyManifest = snapshot?.strategy_manifest;
  const strategyManifestValue = strategyManifest?.value;
  const dataFreshness = snapshot?.data_freshness;
  const strategyEvidence = snapshot?.strategy_evidence;
  const schedulerValue = scheduler?.value ?? scheduler;
  const dataFreshnessValue = dataFreshness?.value ?? schedulerValue;
  const strategyEvidenceValue = strategyEvidence?.value;
  const positions = runtime.positions;
  const exchangeOrders = Array.isArray(runtime.exchangeOrders) ? runtime.exchangeOrders : [];
  const reconciliation = runtime.reconciliation;
  const protections = snapshot?.protections?.value ?? snapshot?.protections ?? [];
  const mismatchActive = mismatch?.status === "available" && mismatch?.value?.consistent === false;
  const unavailable = exchange?.status === "unavailable" || !exchange?.value;
  const stale = exchange?.status === "stale" && Boolean(exchange?.value);
  const exchangePositionCount =
    positions?.exchange?.status === "available" || positions?.exchange?.status === "stale"
      ? listCount(positions.exchange.value?.positions ?? positions.exchange.value)
      : null;
  const localPositionCount =
    positions?.local?.status === "available" || positions?.local?.status === "stale"
      ? listCount(positions.local.value?.positions ?? positions.local.value)
      : null;

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
      {stale ? (
        <div className="truth-stale" role="status">
          交易所数据暂时陈旧，当前展示最后一次可信快照；不会用它授予新开仓权限。
        </div>
      ) : null}
      {entryRuntimeValue?.trading_state === "ENTRY_PAUSED" ? (
        <div className="truth-blocker" role="alert">
          <strong>自动新开仓：暂停</strong>
          <p>{entryPauseExplanation(entryRuntimeValue.entry_authority_reason)}</p>
          <p>调度、已有仓位保护、恢复与 reduce-only 平仓仍正常运行。</p>
        </div>
      ) : null}
      {entryRuntimeValue?.entry_authority === "TESTNET_CANARY" ? (
        <div className="truth-canary" role="status">
          <strong>Testnet Canary 自动交易中</strong>
          <p>Production Strategy 尚未授权；Canary 交易不进入 Production 晋升证据。</p>
        </div>
      ) : null}

      <div className="runtime-truth-grid">
        <article>
          <h3>自动开仓</h3>
          <strong>{entryRuntimeValue?.trading_state === "TRADING" ? "运行中" : "已暂停"}</strong>
          <p>Authority：{entryRuntimeValue?.entry_authority || "NONE"}</p>
          <p>策略：{entryRuntimeValue?.active_entry_strategy || "无"}</p>
          <p>Production：{entryRuntimeValue?.production_authorization_state || "PENDING"}</p>
        </article>
        <article>
          <h3>生产策略</h3>
          <strong>
            {strategyManifestValue?.authorization_state === "PENDING"
              ? "暂无通过验证的生产策略"
              : strategyManifestValue?.strategy_id || "STRATEGY_NOT_READY"}
          </strong>
          <p>版本：{strategyManifestValue?.strategy_version || "无"}</p>
          <p>Rules Hash：{strategyManifestValue?.rules_hash?.slice(0, 12) || "无"}</p>
          <p>执行范围：{(strategyManifestValue?.configured_execution_scope || []).join(" / ") || "无"}</p>
          <p>研究范围：{(strategyManifestValue?.research_symbols || []).join(" / ") || "无"}</p>
          <p>授权：{strategyManifestValue?.authorization_state || "UNKNOWN"}</p>
          <p>研究结论：{strategyManifestValue?.validation_evidence?.conclusion || "UNKNOWN"}</p>
        </article>
        <article>
          <h3>自动平仓</h3>
          <strong>{scheduler?.status === "available" ? "运行中" : "已暂停"}</strong>
          <p>对账 / 保护 / recovery / reduce-only</p>
        </article>
        <article>
          <h3>{symbol}</h3>
          {terminal ? (
            <>
              <strong>{terminal.status === "PASSED" ? "本根 K 线已通过" : "本根 K 线未开仓"}</strong>
              <p>终止阶段：{terminal.entry_gate_result || terminal.terminal_stage || "未评估"}</p>
              <p>原因：{terminal.terminal_reason || terminal.reason_code || "未记录"}</p>
              <p>策略：{terminal.strategy || terminal.strategy_id || "未知"}</p>
              <p>开仓权限：{terminal.entry_authority || "未知"}</p>
              <p>已产生信号：{terminal.signal_generated ? "是" : "否"}</p>
              <p>Entry Gate：{terminal.entry_gate_result || "未评估"}</p>
              <p>订单已提交：{terminal.entry_submitted ? "是" : "否"}</p>
              <small>最后策略判断：{formatTime(terminal.last_decision_at || terminal.bar_time)}</small>
            </>
          ) : (
            <p className="empty-copy">暂无闭合 K 线决策终态，不能推断为“无信号”。</p>
          )}
        </article>
        <article>
          <h3>Binance Testnet</h3>
          <strong>{exchange?.status === "available" ? "已接通" : stale ? "已接通（数据陈旧）" : "未接通"}</strong>
          <DatumMeta datum={exchange} />
        </article>
        <article>
          <h3>Local Projection</h3>
          <strong>{localProjection?.status === "available" ? "已投影" : "未接通"}</strong>
          <DatumMeta datum={localProjection} />
        </article>
        <article>
          <h3>Positions</h3>
          {positions ? (
            <>
              <strong>
                交易所 {exchangePositionCount ?? "不可用"} / 本地 {localPositionCount ?? "不可用"}
              </strong>
              <DatumMeta datum={positions.exchange} />
              <DatumMeta datum={positions.local} />
            </>
          ) : (
            <p className="empty-copy">持仓数据不可用</p>
          )}
        </article>
        <article>
          <h3>Exchange Orders</h3>
          <strong>{listCount(exchangeOrders)} 条</strong>
          {exchangeOrders[0] ? (
            <p>
              最近：{exchangeOrders[0].symbol || "未知"} · {exchangeOrders[0].state || exchangeOrders[0].status || "无状态"}
            </p>
          ) : (
            <p className="empty-copy">暂无交易所订单记录</p>
          )}
        </article>
        <article>
          <h3>Mismatch / Reconciliation</h3>
          <strong>
            {mismatch?.value?.consistent === true
              ? "一致"
              : mismatch?.value?.consistent === false
                ? "不一致"
                : reconciliation?.status || "不可用"}
          </strong>
          <p>
            阻断标的：
            {(reconciliation?.entry_blocked_symbols || []).join(", ") || "无"}
          </p>
          <DatumMeta datum={mismatch} />
        </article>
        <article>
          <h3>Protections</h3>
          <strong>{listCount(protections)} 条</strong>
          {Array.isArray(protections) && protections[0] ? (
            <p>
              最近：{protections[0].symbol || "未知"} · {protections[0].status || "无状态"}
            </p>
          ) : (
            <p className="empty-copy">暂无保护单记录</p>
          )}
        </article>
        <article>
          <h3>Scheduler</h3>
          <strong>{scheduler?.status === "available" ? "运行中" : "离线"}</strong>
          <p>{scheduler?.error || "心跳正常"}</p>
          <DatumMeta datum={scheduler} />
        </article>
        <article>
          <h3>Data Freshness</h3>
          {dataFreshness?.status === "available" || scheduler?.status === "available" ? (
            <>
              <strong>
                数据{dataFreshnessValue?.data_fresh ? "新鲜" : "陈旧"} / 交易所信息
                {dataFreshnessValue?.exchange_info_ready ? "就绪" : "未就绪"}
              </strong>
              <DatumMeta datum={dataFreshness ?? scheduler} />
            </>
          ) : (
            <p className="empty-copy">未接通 / 数据不可用</p>
          )}
        </article>
        <article>
          <h3>Strategy Evidence</h3>
          {strategyEvidence?.status === "available" && strategyEvidenceValue ? (
            <>
              <strong>{strategyEvidenceValue.strategy_lane || "未知通道"}</strong>
              <p>
                标的：
                {(strategyEvidenceValue.acceptance_symbols || []).join(", ") || "无"}
              </p>
              <p>
                采样回退：
                {strategyEvidenceValue.simulation_sampling_fallback_enabled ? "已启用" : "未启用"}
              </p>
              <p>执行模式：{strategyEvidenceValue.execution_mode || "未知"}</p>
              {strategyEvidenceValue.evidence_class ? (
                <small>证据类别：{strategyEvidenceValue.evidence_class}</small>
              ) : null}
              <DatumMeta datum={strategyEvidence} />
            </>
          ) : (
            <p className="empty-copy">未接通 / 数据不可用</p>
          )}
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
