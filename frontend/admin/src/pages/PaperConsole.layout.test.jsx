import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

describe("trading layout contract", () => {
  it("keeps the chart bounded and records inside an internal scroll region", () => {
    const css = readFileSync(resolve(__dirname, "../styles.css"), "utf8");
    expect(css).toContain("height: clamp(280px, 38vh, 420px);");
    expect(css).toContain("min-height: 280px;");
    expect(css).toContain(".records-workspace-scroll");
    expect(css).toContain("overflow: auto;");
    expect(css).toContain("background: #ffffff;");
  });
});
