import { afterEach, describe, expect, it, vi } from "vitest";

import { request } from "./client";

describe("API client", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("turns a network failure into an actionable service-unavailable message", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));

    await expect(request("/api/v1/console/overview")).rejects.toThrow("服务暂时不可用，请稍后重试");
  });

  it("turns a proxy 5xx response without JSON into the same service-unavailable message", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: false,
      status: 502,
      statusText: "Bad Gateway",
      json: vi.fn().mockRejectedValue(new SyntaxError("unexpected token")),
    }));

    await expect(request("/api/v1/console/overview")).rejects.toThrow("服务暂时不可用，请稍后重试");
  });
});
