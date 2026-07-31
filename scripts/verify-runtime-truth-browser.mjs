/**
 * P2 Runtime Truth browser verification (one-shot Playwright).
 * Run: npx -y -p playwright node scripts/verify-runtime-truth-browser.mjs
 */
import { chromium } from "playwright";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, "..");
const LOG_DIR = path.join(ROOT, "logs", "p2-runtime-truth-verify");
const FRONTEND_URL = process.env.FRONTEND_URL ?? "http://127.0.0.1:5173/trading";
const API_BASE = process.env.API_BASE ?? "http://127.0.0.1:8016";
const TOKEN = process.env.ADMIN_TOKEN ?? "dev-admin-token";
const POLL_WAIT_MS = 26_000; // observe at least two 10s V2 refresh intervals

fs.mkdirSync(LOG_DIR, { recursive: true });

const report = {
  timestamp: new Date().toISOString(),
  frontendUrl: FRONTEND_URL,
  apiBase: API_BASE,
  checks: {},
  consoleErrors: [],
  consoleWarnings: [],
  runtimeRequests: [],
  websocketEvents: [],
  evidence: {},
};

function save(name, data) {
  const p = path.join(LOG_DIR, name);
  if (typeof data === "string") fs.writeFileSync(p, data, "utf8");
  else fs.writeFileSync(p, JSON.stringify(data, null, 2), "utf8");
  report.evidence[name] = p;
}

async function apiCheck() {
  const endpoints = [
    "/api/v2/automated-trading/runtime",
    "/api/v2/automated-trading/decisions?limit=5",
    "/api/v2/automated-trading/positions",
    "/api/v2/automated-trading/llm-invocations?limit=5",
    "/api/v2/automated-trading/reconciliation",
  ];
  const datumFields = ["source", "observed_at", "freshness", "status"];
  const results = {};

  for (const ep of endpoints) {
    const url = `${API_BASE}${ep}`;
    const start = Date.now();
    try {
      const res = await fetch(url, {
        headers: { Authorization: `Bearer ${TOKEN}` },
      });
      const elapsed = Date.now() - start;
      const body = await res.json();
      const structure = { status: res.status, elapsed_ms: elapsed };

      if (ep.endsWith("/runtime")) {
        for (const key of ["exchange", "local_projection", "reconciliation", "scheduler"]) {
          const block = body[key];
          structure[key] = block
            ? Object.fromEntries(datumFields.map((f) => [f, block[f] ?? null]))
            : null;
        }
        structure.snapshot_at = body.snapshot_at ?? null;
      } else if (ep.includes("positions")) {
        for (const key of ["exchange", "local_projection"]) {
          const block = body[key];
          structure[key] = block
            ? Object.fromEntries(datumFields.map((f) => [f, block[f] ?? null]))
            : null;
        }
      } else if (ep.includes("reconciliation")) {
        structure.status_field = body.status ?? null;
        structure.observed_at = body.observed_at ?? null;
        structure.entry_blocked_symbols = body.entry_blocked_symbols ?? null;
        structure.error = body.error ?? null;
      } else if (Array.isArray(body)) {
        structure.item_count = body.length;
        const first = body[0];
        structure.first_item_keys = first ? Object.keys(first).slice(0, 12) : [];
      }

      results[ep] = structure;
    } catch (err) {
      results[ep] = { error: String(err) };
    }
  }

  save("api-check.json", results);
  const allOk = Object.values(results).every((r) => r.status === 200 && !r.error);
  report.checks.apiEndpoints = {
    pass: allOk,
    detail: allOk ? "all runtime API endpoints returned 200" : "some endpoints failed",
  };
  return results;
}

function analyzePollingTimestamps(allTimestamps, snapshotTimestamps) {
  const rawTimestamps =
    snapshotTimestamps.length >= 2 ? snapshotTimestamps : allTimestamps;
  // React development StrictMode invokes mount effects twice. Coalesce only
  // the sub-second initialization burst; real polling intervals remain intact.
  const timestamps = rawTimestamps.filter(
    (timestamp, index) => index === 0 || timestamp - rawTimestamps[index - 1] >= 1_000,
  );
  if (timestamps.length < 2) {
    return { pass: false, intervals_ms: [], detail: "fewer than 2 refresh cycles observed" };
  }
  const intervals = [];
  for (let i = 1; i < timestamps.length; i++) intervals.push(timestamps[i] - timestamps[i - 1]);
  const min = Math.min(...intervals);
  const max = Math.max(...intervals);
  const avg = intervals.reduce((a, b) => a + b, 0) / intervals.length;
  const notSpam = min >= 5000;
  const nearFallback = avg >= 8_000 && avg <= 15_000;
  return {
    pass: notSpam && nearFallback,
    refresh_cycles: timestamps.length,
    intervals_ms: intervals,
    min_ms: min,
    max_ms: max,
    avg_ms: Math.round(avg),
    detail: `refresh cycles=${timestamps.length} avg=${Math.round(avg)}ms min=${min}ms max=${max}ms`,
  };
}

async function browserCheck() {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext();
  const page = await context.newPage();

  const runtimeTimestamps = [];
  const snapshotTimestamps = [];

  page.on("console", (msg) => {
    const text = msg.text();
    const entry = { type: msg.type(), text };
    if (msg.type() === "error") report.consoleErrors.push(entry);
    else if (msg.type() === "warning") report.consoleWarnings.push(entry);
  });

  page.on("request", (req) => {
    const url = req.url();
    if (url.includes("/api/v2/automated-trading/")) {
      const ts = Date.now();
      runtimeTimestamps.push(ts);
      if (url.endsWith("/automated-trading/runtime")) snapshotTimestamps.push(ts);
      report.runtimeRequests.push({ url, method: req.method(), ts: new Date().toISOString() });
    }
  });

  page.on("response", async (res) => {
    const url = res.url();
    if (url.includes("/api/v2/automated-trading/")) {
      const existing = report.runtimeRequests.find((r) => r.url === url && !r.status);
      if (existing) existing.status = res.status();
    }
  });

  let wsConnected = false;
  let wsMessages = 0;
  page.on("websocket", (ws) => {
    const wsUrl = ws.url();
    if (!wsUrl.includes("/api/v1/runtime/events")) return;
    ws.on("framesent", (event) => {
      wsMessages += 1;
      report.websocketEvents.push({ direction: "sent", payload: event.payload?.slice(0, 200) });
    });
    ws.on("framereceived", (event) => {
      wsMessages += 1;
      report.websocketEvents.push({ direction: "received", payload: event.payload?.slice(0, 200) });
    });
    ws.on("close", () => {
      report.websocketEvents.push({ direction: "close" });
    });
    wsConnected = true;
  });

  const navStart = Date.now();
  await page.goto(FRONTEND_URL, { waitUntil: "domcontentloaded", timeout: 30_000 });
  await page.waitForTimeout(3_000);
  report.checks.pageLoad = {
    pass: true,
    detail: `loaded in ${Date.now() - navStart}ms`,
  };

  // V2 Runtime Truth panels
  const v2PanelTitles = ["V2 引擎状态", "为什么不开单?", "交易所 vs 本地投影"];
  const panelVisibility = {};
  for (const title of v2PanelTitles) {
    panelVisibility[title] = await page
      .getByText(title, { exact: true })
      .isVisible({ timeout: 15_000 })
      .catch(() => false);
  }
  const panelsVisible = Object.values(panelVisibility).every(Boolean);

  report.checks.runtimeTruthPanel = {
    pass: panelsVisible,
    detail: panelsVisible
      ? `visible: ${v2PanelTitles.join(", ")}`
      : `missing V2 panels: ${v2PanelTitles.filter((title) => !panelVisibility[title]).join(", ")}`,
  };

  // Paper Console fallback sections
  const paperSections = await page.locator("section").count();
  report.checks.paperConsoleSections = {
    pass: paperSections >= 3,
    detail: `${paperSections} section elements found`,
  };

  // Wait to observe polling
  await page.waitForTimeout(POLL_WAIT_MS);

  const polling = analyzePollingTimestamps(runtimeTimestamps, snapshotTimestamps);
  report.checks.pollingInterval = polling;

  report.checks.runtimeNetwork = {
    pass:
      report.runtimeRequests.length >= 1 &&
      report.runtimeRequests.filter((r) => r.status === 200).length >= 1,
    detail: `${report.runtimeRequests.length} runtime requests, ${
      report.runtimeRequests.filter((r) => r.status === 200).length
    } succeeded`,
    requests: report.runtimeRequests,
  };

  report.checks.consoleErrors = {
    pass: report.consoleErrors.length === 0,
    detail:
      report.consoleErrors.length === 0
        ? "no console errors"
        : `${report.consoleErrors.length} console errors`,
  };

  report.checks.websocket = {
    pass: wsConnected,
    detail: wsConnected
      ? `WebSocket connected, ${wsMessages} frames`
      : "no /api/v1/runtime/events WebSocket observed",
    message_count: wsMessages,
  };

  const screenshotPath = path.join(LOG_DIR, "trading-screenshot.png");
  await page.screenshot({ path: screenshotPath, fullPage: true });
  report.evidence["trading-screenshot.png"] = screenshotPath;

  await browser.close();
}

async function main() {
  console.log("P2 Runtime Truth verification starting...");
  console.log(`Log dir: ${LOG_DIR}`);

  await apiCheck();
  try {
    await browserCheck();
  } catch (err) {
    report.checks.browser = { pass: false, detail: String(err) };
    console.error("Browser check failed:", err);
  }

  save("console-errors.json", report.consoleErrors);
  save("console-warnings.json", report.consoleWarnings);
  save("runtime-requests.json", report.runtimeRequests);
  save("websocket-events.json", report.websocketEvents);

  const checklist = {
    timestamp: report.timestamp,
    frontend_up: report.checks.pageLoad?.pass ?? false,
    api_endpoints: report.checks.apiEndpoints?.pass ?? false,
    runtime_truth_panel: report.checks.runtimeTruthPanel?.pass ?? false,
    paper_console_sections: report.checks.paperConsoleSections?.pass ?? false,
    runtime_network_ok: report.checks.runtimeNetwork?.pass ?? false,
    polling_not_spam: report.checks.pollingInterval?.pass ?? false,
    no_console_errors: report.checks.consoleErrors?.pass ?? false,
    websocket_observed: report.checks.websocket?.pass ?? false,
    overall_pass: false,
    checks: report.checks,
    evidence: report.evidence,
  };

  checklist.overall_pass = [
    checklist.frontend_up,
    checklist.api_endpoints,
    checklist.runtime_truth_panel,
    checklist.runtime_network_ok,
    checklist.polling_not_spam,
    checklist.no_console_errors,
  ].every(Boolean);

  save("checklist.json", checklist);
  save("report.json", report);

  console.log("\n=== P2 Runtime Truth Checklist ===");
  for (const [key, val] of Object.entries(checklist)) {
    if (key === "checks" || key === "evidence" || key === "timestamp" || key === "overall_pass") continue;
    console.log(`${val ? "PASS" : "FAIL"} ${key}`);
  }
  console.log(`${checklist.overall_pass ? "PASS" : "FAIL"} overall_pass`);
  console.log(`Evidence: ${LOG_DIR}`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
