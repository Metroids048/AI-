import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { StrategyLibrary } from "./StrategyLibrary";

const { request } = vi.hoisted(() => ({ request: vi.fn() }));

vi.mock("../api/client", () => ({ request }));

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter><StrategyLibrary /></MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("StrategyLibrary", () => {
  it("registers a real research idea through the lifecycle API", async () => {
    request.mockImplementation(async (path, options) => {
      if (path === "/api/v1/strategies/ideas" && options?.method === "POST") return { idea_id: "idea-1" };
      if (path === "/api/v1/strategies" || path === "/api/v1/strategies/drafts" || path === "/api/v1/strategies/ideas") return { items: [] };
      throw new Error(`unexpected path ${path}`);
    });

    renderPage();
    await screen.findByText("策略库");
    fireEvent.click(screen.getByRole("button", { name: "登记研究想法" }));
    fireEvent.change(screen.getByLabelText("想法标题"), { target: { value: "Funding carry" } });
    fireEvent.change(screen.getByLabelText("可验证核心假设"), { target: { value: "Funding net edge remains positive" } });
    fireEvent.click(screen.getByRole("button", { name: "登记" }));

    await waitFor(() => {
      expect(request).toHaveBeenCalledWith("/api/v1/strategies/ideas", expect.objectContaining({ method: "POST" }));
    });
  });
});
