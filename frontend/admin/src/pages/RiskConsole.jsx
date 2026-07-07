import { useQuery } from "@tanstack/react-query";

import { request } from "../api/client";
import { asArray, formatNumber, formatTime } from "../utils/format";

export function RiskConsole() {
  const profiles = useQuery({ queryKey: ["risk-profiles"], queryFn: () => request("/api/v1/risk/profiles"), staleTime: 15000 });
  const events = useQuery({ queryKey: ["risk-events"], queryFn: () => request("/api/v1/risk/events?active_only=false"), refetchInterval: 10000 });
  const profileRows = asArray(profiles.data?.items);
  const eventRows = asArray(events.data?.items);

  return (
    <main className="app-shell page-shell">
      <header className="page-header">
        <p className="eyebrow">Risk Layer</p>
        <h1>风控控制台</h1>
      </header>
      <section className="records-grid">
        <div className="exchange-panel table-panel">
          <div className="panel-title"><h2>RiskProfile</h2><span>{profileRows.length}</span></div>
          <table>
            <thead>
              <tr>
                <th>ID</th>
                <th>单品种</th>
                <th>总敞口</th>
                <th>最大杠杆</th>
                <th>硬停止回撤</th>
              </tr>
            </thead>
            <tbody>
              {profileRows.length ? profileRows.map((item) => (
                <tr key={item.risk_profile_id ?? item.profile_name}>
                  <td>{item.risk_profile_id ?? item.profile_name ?? "-"}</td>
                  <td>{formatNumber(item.max_symbol_exposure, 3)}</td>
                  <td>{formatNumber(item.max_total_exposure, 3)}</td>
                  <td>{formatNumber(item.max_leverage, 2)}</td>
                  <td>{formatNumber(item.hard_stop_drawdown_limit, 3)}</td>
                </tr>
              )) : <tr><td colSpan="5">暂无风控配置</td></tr>}
            </tbody>
          </table>
        </div>
        <div className="exchange-panel table-panel">
          <div className="panel-title"><h2>风险事件流</h2><span>{eventRows.length}</span></div>
          <table>
            <thead>
              <tr>
                <th>时间</th>
                <th>等级</th>
                <th>类型</th>
                <th>状态</th>
                <th>说明</th>
              </tr>
            </thead>
            <tbody>
              {eventRows.length ? eventRows.map((item) => (
                <tr key={item.risk_event_id ?? item.description}>
                  <td>{formatTime(item.occurred_at)}</td>
                  <td>{item.severity}</td>
                  <td>{item.event_type}</td>
                  <td>{item.resolution_status}</td>
                  <td>{item.description}</td>
                </tr>
              )) : <tr><td colSpan="5">暂无风险事件</td></tr>}
            </tbody>
          </table>
        </div>
      </section>
    </main>
  );
}
