import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { StrategyLibrary } from "./StrategyLibrary";

const { request } = vi.hoisted(() => ({ request: vi.fn() }));

vi.mock("../api/client", () => ({ request }));

afterEach(() => {
  cleanup();
  request.mockReset();
});

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter><StrategyLibrary /></MemoryRouter>
    </QueryClientProvider>,
  );
}

const playbook = {
  metadata: { verified_on: "2026-07-12", verified_commit: "80ce3d6", source_documents: ["02_量化策略与LLM+RAG开平单逻辑详细报告.md"] },
  channels: [
    { channel_id: "funding_carry", name: "资金费率套利", positioning: "市场中性", core_assumption: "净资金费率覆盖成本", maturity: "mature" },
    { channel_id: "technical_directional", name: "技术方向性", positioning: "方向策略", core_assumption: "多信号确认", maturity: "iterating" },
  ],
  decision_stages: [
    { stage_id: "new_bar", name: "新K线", description: "仅处理已收线数据" },
    { stage_id: "gatekeeper", name: "Gatekeeper", description: "纯规则风控" },
    { stage_id: "exchange_order", name: "下单", description: "Gateway确认" },
  ],
  technical_signals: ["MACD", "Dow", "价格行为", "RSI", "EMA", "ADX", "VWAP", "Bollinger"].map((name, index) => ({ signal_id: `s${index}`, name, parameters: {}, trigger: `${name}触发`, role: "确认" })),
  exit_rules: [{ rule_id: "stop-first", name: "同K线优先止损", priority: 1, description: "止损与止盈同时触发时先止损" }],
  position_sizing: { formula: "账户权益 x 单笔风险 / 止损距离", defaults: [{ key: "operator_risk_per_trade", value: 0.01, scope: "operator", source_ref: "bootstrap" }], limitations: ["组合相关性风险尚未接入"] },
  llm_rag: { allowed: ["二元否决"], forbidden: ["决定方向"], retrieval_mode: "keyword_overlap", provider_chain: ["Anthropic", "OpenRouter", "GitHub Models"], limitations: ["不是向量检索"] },
  external_sources: [{ source_id: "superalgos", name: "Superalgos", repo_url: "https://github.com/Superalgos/Superalgos", license: "Apache-2.0", license_policy: "distilled_research_allowed", absorbable_content: "分阶段策略生命周期", platform_mapping: "Strategy Layer", implementation_status: "indexed_only" }],
  roadmap: [{ item_id: "meta-label-oos-validation", title: "MetaLabel样本外验证", priority: "P0", status: "pending", description: "扩展样本外验证", optimization_target: "MetaLabel", note: null, updated_by: null, updated_at: null, audit_history: [] }],
};

function defaultRequest(path, options) {
  if (path === "/api/v1/strategy-library/playbook") return Promise.resolve(playbook);
  if (path === "/api/v1/strategies" || path === "/api/v1/strategies/drafts" || path === "/api/v1/strategies/ideas") return Promise.resolve({ items: [] });
  if (path.includes("/roadmap-items/") && options?.method === "PATCH") return Promise.resolve({ ...playbook.roadmap[0], status: "in_progress" });
  throw new Error(`unexpected path ${path}`);
}

describe("StrategyLibrary", () => {
  it("renders all playbook tabs from the backend payload", async () => {
    request.mockImplementation(defaultRequest);
    renderPage();

    await screen.findByText("资金费率套利");
    for (const tab of ["策略资产", "策略总览", "开单逻辑", "平单逻辑", "仓位管理", "LLM 与 RAG 边界", "外部策略来源", "优化路线图"]) {
      expect(screen.getByRole("tab", { name: tab })).toBeInTheDocument();
    }
    expect(screen.getByText(/最后核对.*2026-07-12/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "开单逻辑" }));
    expect(screen.getByText("MACD")).toBeInTheDocument();
    expect(screen.getByText("Gatekeeper")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "外部策略来源" }));
    expect(screen.getByText("Apache-2.0")).toBeInTheDocument();
    expect(screen.getByText("distilled_research_allowed")).toBeInTheDocument();
  });

  it("persists roadmap status through the controlled endpoint", async () => {
    request.mockImplementation(defaultRequest);
    renderPage();
    await screen.findByText("资金费率套利");
    fireEvent.click(screen.getByRole("tab", { name: "优化路线图" }));
    fireEvent.change(screen.getByLabelText("MetaLabel样本外验证状态"), { target: { value: "in_progress" } });
    await waitFor(() => expect(request).toHaveBeenCalledWith(
      "/api/v1/strategy-library/roadmap-items/meta-label-oos-validation",
      expect.objectContaining({ method: "PATCH" }),
    ));
  });

  it("shows an explicit playbook error and retries on command", async () => {
    request.mockImplementation((path) => {
      if (path === "/api/v1/strategy-library/playbook") return Promise.reject(new Error("API unavailable"));
      if (path === "/api/v1/strategies" || path === "/api/v1/strategies/drafts" || path === "/api/v1/strategies/ideas") return Promise.resolve({ items: [] });
      throw new Error(`unexpected path ${path}`);
    });
    renderPage();

    expect(await screen.findByText(/策略说明加载失败：API unavailable/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    await waitFor(() => expect(request.mock.calls.filter(([path]) => path === "/api/v1/strategy-library/playbook")).toHaveLength(2));
  });

  it("reports a roadmap update failure without hiding the current state", async () => {
    request.mockImplementation((path, options) => {
      if (path === "/api/v1/strategy-library/playbook") return Promise.resolve(playbook);
      if (path === "/api/v1/strategies" || path === "/api/v1/strategies/drafts" || path === "/api/v1/strategies/ideas") return Promise.resolve({ items: [] });
      if (path.includes("/roadmap-items/") && options?.method === "PATCH") return Promise.reject(new Error("save rejected"));
      throw new Error(`unexpected path ${path}`);
    });
    renderPage();
    await screen.findByText("资金费率套利");
    fireEvent.click(screen.getByRole("tab", { name: "优化路线图" }));
    fireEvent.change(screen.getByLabelText("MetaLabel样本外验证状态"), { target: { value: "in_progress" } });

    expect(await screen.findByText("路线图状态保存失败：save rejected")).toBeInTheDocument();
    expect(screen.getByLabelText("MetaLabel样本外验证状态")).toHaveValue("pending");
  });

  it("registers a real research idea through the lifecycle API", async () => {
    request.mockImplementation(async (path, options) => {
      if (path === "/api/v1/strategy-library/playbook") return playbook;
      if (path === "/api/v1/strategies/ideas" && options?.method === "POST") return { idea_id: "idea-1" };
      if (path === "/api/v1/strategies" || path === "/api/v1/strategies/drafts" || path === "/api/v1/strategies/ideas") return { items: [] };
      throw new Error(`unexpected path ${path}`);
    });

    renderPage();
    await screen.findByText("资金费率套利");
    fireEvent.click(screen.getByRole("tab", { name: "策略资产" }));
    fireEvent.click(screen.getByRole("button", { name: "登记研究想法" }));
    fireEvent.change(screen.getByLabelText("想法标题"), { target: { value: "Funding carry" } });
    fireEvent.change(screen.getByLabelText("可验证核心假设"), { target: { value: "Funding net edge remains positive" } });
    fireEvent.click(screen.getByRole("button", { name: "登记" }));

    await waitFor(() => {
      expect(request).toHaveBeenCalledWith("/api/v1/strategies/ideas", expect.objectContaining({ method: "POST" }));
    });
  });
});
