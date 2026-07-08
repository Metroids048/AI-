import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useParams } from "react-router-dom";

import { request } from "../api/client";
import { ActionMessage, DetailHeader, JsonPanel } from "../components/DetailPanels";
import { Metric } from "../components/Common";
import { asArray } from "../utils/format";

export function ResearchSourceDetail() {
  const { sourceId } = useParams();
  const queryClient = useQueryClient();
  const [actionMessage, setActionMessage] = useState("");

  const source = useQuery({
    queryKey: ["research-source", sourceId],
    queryFn: () => request(`/api/v1/research-sources/${sourceId}`),
    enabled: Boolean(sourceId),
  });
  const assets = useQuery({
    queryKey: ["research-source-assets", sourceId],
    queryFn: () => request(`/api/v1/research-sources/${sourceId}/assets`),
    enabled: Boolean(sourceId),
  });

  const refreshAssets = useMutation({
    mutationFn: () => request(`/api/v1/research-sources/${sourceId}/refresh-assets`, { method: "POST", body: "{}" }),
    onSuccess: (payload) => {
      setActionMessage(`资产刷新完成：${payload?.imported_sources?.length ?? 0} 源处理。`);
      queryClient.invalidateQueries({ queryKey: ["research-source-assets", sourceId] });
    },
    onError: (err) => setActionMessage(`资产刷新失败：${err.message}`),
  });

  const extractIdeas = useMutation({
    mutationFn: () => request(`/api/v1/research-sources/${sourceId}/extract-ideas`, {
      method: "POST",
      body: JSON.stringify({ max_ideas: 5 }),
    }),
    onSuccess: (payload) => {
      setActionMessage(`已提取 ${asArray(payload?.items).length} 条策略想法。`);
      queryClient.invalidateQueries({ queryKey: ["research-strategy-ideas"] });
    },
    onError: (err) => setActionMessage(`提取想法失败：${err.message}`),
  });

  const assetRows = asArray(assets.data?.items);
  const data = source.data ?? {};

  return (
    <main className="app-shell page-shell">
      <DetailHeader eyebrow="Research Source" title={sourceId} backTo="/research" />
      <ActionMessage message={actionMessage} />
      {source.isError ? <div className="action-line">加载失败：{source.error.message}</div> : null}
      <section className="funding-metrics">
        <Metric label="类型" value={data.source_type ?? data.category ?? "-"} />
        <Metric label="许可证" value={data.license ?? data.license_policy ?? "-"} />
        <Metric label="资产数" value={assetRows.length} />
        <Metric label="状态" value={data.ingestion_status ?? data.status ?? "-"} />
      </section>
      <section className="form-row">
        <button type="button" onClick={() => refreshAssets.mutate()} disabled={refreshAssets.isPending}>刷新资产</button>
        <button type="button" onClick={() => extractIdeas.mutate()} disabled={extractIdeas.isPending}>提取想法</button>
      </section>
      <JsonPanel title="Source Manifest" value={data} />
      <section className="exchange-panel table-panel">
        <div className="panel-title"><h2>本地资产</h2><span>{assetRows.length}</span></div>
        <table>
          <thead>
            <tr>
              <th>Asset</th>
              <th>Status</th>
              <th>Path</th>
              <th>Size</th>
            </tr>
          </thead>
          <tbody>
            {assetRows.length ? assetRows.map((item) => (
              <tr key={item.asset_id ?? item.local_path}>
                <td>{item.asset_id ?? item.title ?? "-"}</td>
                <td>{item.status ?? "-"}</td>
                <td>{item.local_path ?? "-"}</td>
                <td>{item.byte_size ?? "-"}</td>
              </tr>
            )) : <tr><td colSpan="4">暂无本地资产</td></tr>}
          </tbody>
        </table>
      </section>
    </main>
  );
}
