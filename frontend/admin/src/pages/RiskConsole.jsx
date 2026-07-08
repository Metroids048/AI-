import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { request } from "../api/client";
import { RiskEventFeed } from "../components/RuntimePanels";
import { RiskProfileForm } from "../components/RiskProfileForm";
import { ActionMessage } from "../components/DetailPanels";
import { asArray, formatNumber } from "../utils/format";

export function RiskConsole() {
  const queryClient = useQueryClient();
  const [actionMessage, setActionMessage] = useState("");
  const [editingProfile, setEditingProfile] = useState(null);
  const [showCreateForm, setShowCreateForm] = useState(false);

  const profiles = useQuery({ queryKey: ["risk-profiles"], queryFn: () => request("/api/v1/risk/profiles"), staleTime: 15000 });
  const events = useQuery({ queryKey: ["risk-events"], queryFn: () => request("/api/v1/risk/events?active_only=false"), refetchInterval: 10000 });
  const profileRows = asArray(profiles.data?.items);
  const eventRows = asArray(events.data?.items);

  const refreshProfiles = async () => {
    await queryClient.invalidateQueries({ queryKey: ["risk-profiles"] });
  };

  const handleResolve = async (riskEventId, resolutionStatus) => {
    if (!riskEventId) return;
    try {
      await request(`/api/v1/risk/events/${riskEventId}/resolution`, {
        method: "PATCH",
        body: JSON.stringify({ resolution_status: resolutionStatus }),
      });
      setActionMessage(resolutionStatus === "resolved" ? "风控事件已恢复。" : "风控事件已确认。");
      await queryClient.invalidateQueries({ queryKey: ["risk-events"] });
    } catch (err) {
      setActionMessage(`风控事件操作失败：${err.message}`);
    }
  };

  const handleCreateProfile = async (form) => {
    try {
      await request("/api/v1/risk/profiles", {
        method: "POST",
        body: JSON.stringify(form),
      });
      setActionMessage("RiskProfile 已创建。");
      setShowCreateForm(false);
      await refreshProfiles();
    } catch (err) {
      setActionMessage(`创建失败：${err.message}`);
    }
  };

  const handleUpdateProfile = async (form) => {
    if (!editingProfile?.risk_profile_id) return;
    try {
      const { risk_profile_id: _ignored, ...payload } = form;
      await request(`/api/v1/risk/profiles/${editingProfile.risk_profile_id}`, {
        method: "PUT",
        body: JSON.stringify(payload),
      });
      setActionMessage("RiskProfile 已更新。");
      setEditingProfile(null);
      await refreshProfiles();
    } catch (err) {
      setActionMessage(`更新失败：${err.message}`);
    }
  };

  return (
    <main className="app-shell page-shell">
      <header className="page-header">
        <p className="eyebrow">Risk Layer</p>
        <h1>风控控制台</h1>
      </header>
      <ActionMessage message={actionMessage} />
      <section className="form-row">
        <button type="button" onClick={() => { setShowCreateForm((current) => !current); setEditingProfile(null); }}>
          {showCreateForm ? "收起新建表单" : "新建 RiskProfile"}
        </button>
      </section>
      {showCreateForm ? (
        <RiskProfileForm
          onSubmit={handleCreateProfile}
          onCancel={() => setShowCreateForm(false)}
          submitLabel="创建"
        />
      ) : null}
      {editingProfile ? (
        <RiskProfileForm
          initialProfile={editingProfile}
          onSubmit={handleUpdateProfile}
          onCancel={() => setEditingProfile(null)}
          submitLabel="更新"
        />
      ) : null}
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
                <th>操作</th>
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
                  <td>
                    <button type="button" onClick={() => { setEditingProfile(item); setShowCreateForm(false); }}>
                      编辑
                    </button>
                  </td>
                </tr>
              )) : <tr><td colSpan="6">暂无风控配置</td></tr>}
            </tbody>
          </table>
        </div>
        <RiskEventFeed events={eventRows} onResolve={handleResolve} />
      </section>
    </main>
  );
}
