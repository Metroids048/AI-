import { useQuery } from "@tanstack/react-query";

import { request } from "../api/client";
import { Metric } from "../components/Common";
import { asArray, formatNumber, formatPercent, formatTime } from "../utils/format";

const FUNDING_PARAMS = new URLSearchParams({
  symbol: "BTC/USDT",
  perp_symbol: "BTC/USDT:USDT",
  timeframe: "1m",
});

export function ValidationCenter() {
  const backtests = useQuery({
    queryKey: ["validation-backtests"],
    queryFn: () => request("/api/v1/backtests"),
    refetchInterval: 15000,
  });
  const optimizations = useQuery({
    queryKey: ["validation-optimizations"],
    queryFn: () => request("/api/v1/optimizations"),
    refetchInterval: 15000,
  });
  const hypotheses = useQuery({
    queryKey: ["validation-hypotheses"],
    queryFn: () => request("/api/v1/validation/hypotheses"),
    refetchInterval: 15000,
  });
  const funding = useQuery({
    queryKey: ["validation-funding-signal"],
    queryFn: () => request(`/api/v1/market/funding-arbitrage-signal?${FUNDING_PARAMS.toString()}`),
    refetchInterval: 10000,
  });

  const backtestRows = asArray(backtests.data?.items);
  const optimizationRows = asArray(optimizations.data?.items);
  const hypothesisRows = asArray(hypotheses.data?.items);

  return (
    <main className="app-shell page-shell">
      <header className="page-header">
        <p className="eyebrow">Validation Layer</p>
        <h1>验证中心</h1>
      </header>

      <section className="funding-metrics">
        <Metric label="Funding 信号源" value={funding.data?.source ?? "等待 Binance 数据"} />
        <Metric label="年化 Carry" value={formatPercent(funding.data?.annualized_carry)} />
        <Metric label="准入状态" value={funding.data?.signal_status ?? "pending"} />
        <Metric label="样本时间" value={formatTime(funding.data?.generated_at)} />
      </section>

      <section className="records-grid">
        <RecordTable
          title="历史回测"
          rows={backtestRows}
          columns={[
            ["backtest_run_id", "Run ID"],
            ["run_status", "状态"],
            ["execution_engine", "引擎"],
            ["strategy_id", "策略"],
            ["created_at", "创建时间", formatTime],
          ]}
        />
        <RecordTable
          title="优化任务"
          rows={optimizationRows}
          columns={[
            ["optimization_run_id", "Run ID"],
            ["run_status", "状态"],
            ["optimization_method", "方法"],
            ["strategy_id", "策略"],
          ]}
        />
      </section>

      <section className="exchange-panel table-panel">
        <div className="panel-title"><h2>验证假设</h2><span>{hypothesisRows.length}</span></div>
        <table>
          <thead>
            <tr>
              <th>ID</th>
              <th>策略</th>
              <th>状态</th>
              <th>市场</th>
              <th>核心假设</th>
            </tr>
          </thead>
          <tbody>
            {hypothesisRows.length ? hypothesisRows.map((item) => (
              <tr key={item.hypothesis_id}>
                <td>{item.hypothesis_id}</td>
                <td>{item.strategy_id ?? item.idea_id ?? "-"}</td>
                <td>{item.status ?? "-"}</td>
                <td>{item.market ?? "-"}</td>
                <td>{item.core_thesis ?? item.hypothesis_text ?? "-"}</td>
              </tr>
            )) : <tr><td colSpan="5">暂无验证假设</td></tr>}
          </tbody>
        </table>
      </section>
    </main>
  );
}

function RecordTable({ title, rows, columns }) {
  return (
    <div className="exchange-panel table-panel">
      <div className="panel-title"><h2>{title}</h2><span>{rows.length}</span></div>
      <table>
        <thead>
          <tr>{columns.map(([, label]) => <th key={label}>{label}</th>)}</tr>
        </thead>
        <tbody>
          {rows.length ? rows.map((row, index) => (
            <tr key={row.backtest_run_id ?? row.optimization_run_id ?? index}>
              {columns.map(([key, label, formatter]) => {
                const value = row[key];
                return <td key={label}>{formatter ? formatter(value) : formatCell(value)}</td>;
              })}
            </tr>
          )) : <tr><td colSpan={columns.length}>暂无记录</td></tr>}
        </tbody>
      </table>
    </div>
  );
}

function formatCell(value) {
  if (typeof value === "number") return formatNumber(value);
  if (value === null || value === undefined || value === "") return "-";
  return String(value);
}
