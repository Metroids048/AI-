import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const css = readFileSync(resolve(__dirname, "../styles.css"), "utf8");

describe("trading layout contract", () => {
  it("keeps the chart bounded and records inside an internal scroll region", () => {
    expect(css).toContain("height: clamp(280px, 38vh, 420px);");
    expect(css).toContain("min-height: 280px;");
    expect(css).toContain(".records-workspace-scroll");
    expect(css).toContain("overflow: auto;");
    expect(css).toContain("background: #ffffff;");
  });

  // T-009: the frozen kline sizing must not be re-broken by a fixed height.
  // The 500px/610px minimums previously pushed the records tabs below the fold
  // at 900p, which is the specific regression this guards.
  it("has no fixed 500px or 610px height on trading or chart containers", () => {
    const offenders = [];
    const blockPattern = /(\.[a-z0-9-]+(?:\s*,\s*\.[a-z0-9-]+)*)\s*\{([^}]*)\}/gi;
    let match;

    while ((match = blockPattern.exec(css)) !== null) {
      const [, selector, body] = match;
      if (!/trading|kline|chart|ticket|workspace/i.test(selector)) continue;

      const fixed = body.match(/(?:min-)?height:\s*(500px|610px)\s*;/g);
      if (fixed) offenders.push(`${selector.trim()} -> ${fixed.join(" ")}`);
    }

    expect(offenders, `fixed heights reintroduced: ${offenders.join(" | ")}`).toEqual([]);
  });

  it("bounds the chart so the records tabs fit the first fold at 900p and 1080p", () => {
    // clamp(280px, 38vh, 420px) is the frozen contract. Resolve it per viewport
    // and confirm the chart cannot consume the space the records tabs need.
    const clampMatch = css.match(/height:\s*clamp\((\d+)px,\s*(\d+)vh,\s*(\d+)px\)/);
    expect(clampMatch, "kline clamp declaration is missing").toBeTruthy();

    const [, minPx, vh, maxPx] = clampMatch.map(Number);

    for (const viewportHeight of [900, 1080]) {
      const preferred = (vh / 100) * viewportHeight;
      const resolved = Math.min(Math.max(preferred, minPx), maxPx);

      // The chart must never take more than half the fold, otherwise account
      // status + chart + records tabs cannot coexist above it.
      expect(
        resolved,
        `chart resolves to ${resolved}px at ${viewportHeight}p, over half the fold`,
      ).toBeLessThanOrEqual(viewportHeight / 2);

      // And it must stay legible rather than collapsing.
      expect(resolved).toBeGreaterThanOrEqual(280);
    }
  });

  it("keeps long record areas scrolling internally rather than stretching the page", () => {
    const scrollBlock = css.match(/\.records-workspace-scroll\s*\{([^}]*)\}/);
    expect(scrollBlock, ".records-workspace-scroll block is missing").toBeTruthy();

    const body = scrollBlock[1];
    expect(body).toMatch(/overflow:\s*auto;/);
    // An internal scroll region needs a bound, or it just grows the document.
    expect(body).toMatch(/max-height|height/);
  });
});
