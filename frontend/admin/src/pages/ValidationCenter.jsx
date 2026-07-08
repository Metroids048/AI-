import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { request } from "../api/client";
import { ActionMessage, StatusBadge } from "../components/DetailPanels";
import { Metric } from "../components/Common";
import { asArray, formatNumber, formatPercent, formatTime } from "../utils/format";

const FUNDING_PARAMS = new URLSearchParams({
  symbol: "BTC/USDT",
  perp_symbol: "BTC/USDT:USDT",
  timeframe: "1m",
});

export function ValidationCenter() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [actionMessage, setActionMessage] = useState("");
  const [submitForm, setSubmitForm] = useState({ strategy_id: "", execution_engine: "freqtrade" });

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

  const handleSubmitBacktest = async (event) => {
    event.preventDefault();
    if (!submitForm.strategy_id.trim()) {
      setActionMessage("提交回测需要 strategy_id。");
      return;
    }
    try {
      const payload = await request("/api/v1/backtests", {
        method: "POST",
        body: JSON.stringify(submitForm),
      });
      setActionMessage(`回测已排队：${payload.resource_id ?? payload.task_id}`);
      await queryClient.invalidateQueries({ queryKey: ["validation-backtests"] });
    } catch (err) {
      setActionMessage(`提交回测失败：${err.message}`);
    }
  };

  return (
    <main className="app-shell page-shell">
      <header className="page-header">
        <p className="eyebrow">Validation Layer</p>
        <h1>验证中心</h1>
      </header>
      <ActionMessage message={actionMessage} />
      {(backtests.isError || optimizations.isError) ? (
        <div className="action-line">数据加载失败：{backtests.error?.message ?? optimizations.error?.message}</div>
      ) : null}

      <section className="exchange-panel form-panel">
        <div className="panel-title"><h2>提交回测</h2></div>
        <form className="form-row" onSubmit={handleSubmitBacktest}>
          <input
            placeholder="strategy_id"
            value={submitForm.strategy_id}
            onChange={(event) => setSubmitForm((current) => ({ ...current, strategy_id: event.target.value }))}
          />
          <input
            placeholder="execution_engine"
            value={submitForm.execution_engine}
            onChange={(event) => setSubmitForm((current) => ({ ...current, execution_engine: event.target.value }))}
          />
          <button type="submit">提交</button>
        </form>
      </section>

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
          onRowClick={(row) => row.backtest_run_id && navigate(`/validation/backtests/${row.backtest_run_id}`)}
          columns={[
            ["backtest_run_id", "Run ID"],
            ["run_status", "状态", (value) => <StatusBadge value={value} />],
            ["strategy_id", "策略"],
            ["metrics_summary.sharpe", "Sharpe", (value, row) => formatNumber(row.metrics_summary?.sharpe, 3)],
            ["created_at", "创建时间", formatTime],
          ]}
        />
        <RecordTable
          title="优化任务"
          rows={optimizationRows}
          onRowClick={(row) => row.optimization_run_id && navigate(`/validation/optimizations/${row.optimization_run_id}`)}
          columns={[
            ["optimization_run_id", "Run ID"],
            ["run_status", "状态", (value) => <StatusBadge value={value} />],
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

function RecordTable({ title, rows, columns, onRowClick }) {
  return (
    <div className="exchange-panel table-panel">
      <div className="panel-title"><h2>{title}</h2><span>{rows.length}</span></div>
      <table>
        <thead>
          <tr>{columns.map(([, label]) => <th key={label}>{label}</th>)}</tr>
        </thead>
        <tbody>
          {rows.length ? rows.map((row, index) => (
            <tr
              key={row.backtest_run_id ?? row.optimization_run_id ?? index}
              className={onRowClick ? "clickable-row" : undefined}
              onClick={onRowClick ? () => onRowClick(row) : undefined}
            >
              {columns.map(([key, label, formatter]) => {
                const value = key.includes(".")
                  ? key.split(".").reduce((current, part) => current?.[part], row)
                  : row[key];
                return <td key={label}>{formatter ? formatter(value, row) : formatCell(value)}</td>;
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
