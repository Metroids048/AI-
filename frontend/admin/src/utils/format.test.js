import { readdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import { formatBoolean, formatEnum, formatFieldLabel, formatNoTradeSummary } from "./format";

describe("presentation mapping", () => {
  it("maps protocol enums and field names without changing machine values", () => {
    expect(formatEnum("configured")).toBe("已配置");
    expect(formatEnum("grid_search")).toBe("网格搜索");
    expect(formatFieldLabel("execution_engine")).toBe("执行引擎");
    expect(formatBoolean(false)).toBe("否");
    expect(formatEnum("BTC")).toBe("BTC");
  });

  // T-008: assert the contract's full enum list, not a sample of it.
  it("maps every contract-listed internal enum to Chinese", () => {
    const required = {
      pending: "待处理",
      healthy: "正常",
      degraded: "异常/降级",
      configured: "已配置",
      missing: "未配置",
      long: "做多",
      short: "做空",
      ACTIVE: "已启用",
      BINANCE_TESTNET: "币安模拟盘",
    };

    for (const [token, expected] of Object.entries(required)) {
      expect(formatEnum(token), `enum ${token}`).toBe(expected);
    }
  });

  it("maps every contract-listed field label to Chinese", () => {
    const required = {
      execution_engine: "执行引擎",
      optimization_method: "优化方式",
      implementation_status: "接入状态",
      scheduler_error: "调度器异常",
      next_cycle_eta_seconds: "距下次执行",
      risk_profile: "风控配置",
      operator_risk_per_trade: "单笔风险比例",
      generic_risk_profile_max_leverage: "默认最大杠杆",
    };

    for (const [field, expected] of Object.entries(required)) {
      // The label must carry the contract wording; a unit suffix such as
      // 距下次执行（秒） is a permitted refinement, not a deviation.
      expect(formatFieldLabel(field), `field ${field}`).toContain(expected);
    }
  });

  it("never silently maps an unknown enum to a healthy-sounding value", () => {
    expect(formatEnum("some_unknown_state")).not.toBe("正常");
    expect(formatEnum(undefined)).not.toBe("正常");
    expect(formatEnum(null)).not.toBe("正常");
  });

  it("formats deterministic no-trade summary codes for the Chinese homepage", () => {
    expect(formatNoTradeSummary("SCHEDULER_OFFLINE")).toBe("自动交易程序异常：调度器心跳中断");
    expect(formatNoTradeSummary("HEALTHY_WAITING_FOR_SIGNAL")).toBe("策略正在等待交易机会");
    expect(formatNoTradeSummary("ENTRY_BLOCKED", "PRICE_DRIFT_EXCEEDED", 4)).toBe("新开仓被拦截：价格漂移超限（4 次）");
    expect(formatNoTradeSummary("NO_FORWARD_VALIDATION_CANDIDATE")).toBe(
      "新开仓授权阻塞：暂无通过 Forward Validation 的候选",
    );
  });

  it("preserves professional terms and proper nouns verbatim", () => {
    const allowlist = [
      "BTC",
      "ETH",
      "SOL",
      "Binance",
      "LLM",
      "RAG",
      "API",
      "MACD",
      "RSI",
      "EMA",
      "ADX",
      "VWAP",
      "Sharpe",
      "Freqtrade",
      "Hyperopt",
    ];
    for (const term of allowlist) {
      expect(formatEnum(term), `allowlisted ${term}`).toBe(term);
    }
  });

  it("is the single presentation mapping owner (no parallel i18n layer)", () => {
    const srcDir = resolve(__dirname, "..");
    const dirNames = readdirSync(srcDir, { withFileTypes: true })
      .filter((entry) => entry.isDirectory())
      .map((entry) => entry.name);

    expect(dirNames).not.toContain("i18n");
    expect(dirNames).not.toContain("locales");

    if (dirNames.includes("lib")) {
      expect(readdirSync(resolve(srcDir, "lib"))).not.toContain("format.js");
    }
  });
});

// T-008: user-visible DOM must not leak raw internal tokens on the seven main
// navigation pages. Scans JSX text nodes instead of trusting a prior summary.
describe("T-008 internal enums are not bare in user DOM", () => {
  const MAIN_NAV_PAGES = [
    "PaperConsole.jsx",
    "StrategyLibrary.jsx",
    "ReviewCenter.jsx",
    "RiskConsole.jsx",
    "ValidationCenter.jsx",
    "ResearchDesk.jsx",
    "OpsConsole.jsx",
  ];

  const FORBIDDEN_TEXT = [
    "not_probed",
    "ENSEMBLE_DISCARDED",
    "TECHNICAL_SIGNALS_INSUFFICIENT",
    "BINANCE_TESTNET",
    "grid_search",
    "implementation_status",
    "scheduler_error",
    "optimization_method",
  ];

  /**
   * Literal text between JSX tags, e.g. `>pending<`. Attributes, object keys,
   * API paths and className values are machine values the contract allows, so
   * they are deliberately not matched here.
   */
  function jsxTextNodes(source) {
    const nodes = [];
    const pattern = />([^<>{}\n]+)</g;
    let match;
    while ((match = pattern.exec(source)) !== null) {
      const text = match[1].trim();
      if (text) nodes.push(text);
    }
    return nodes;
  }

  for (const page of MAIN_NAV_PAGES) {
    it(`${page} renders no bare internal tokens`, () => {
      const source = readFileSync(resolve(__dirname, "../pages", page), "utf8");
      const texts = jsxTextNodes(source);

      for (const token of FORBIDDEN_TEXT) {
        expect(
          texts.filter((text) => text === token),
          `${page} leaks "${token}" as user-visible text`,
        ).toEqual([]);
      }
    });
  }
});
