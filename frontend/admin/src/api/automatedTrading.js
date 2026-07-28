/**
 * V2 Automated Trading API client (plan section 13).
 *
 * Single source of truth — replaces multi-endpoint guessing in useConsoleData.
 * All calls hit /api/v2/automated-trading/*.
 */
import { request } from "./client";

const V2_BASE = "/api/v2/automated-trading";

/** Fetch the full runtime snapshot (the one call that tells you everything). */
export async function fetchRuntimeSnapshot() {
  return request(`${V2_BASE}/runtime`);
}

/** Fetch recent cycles, newest first. */
export async function fetchCycles(limit = 20) {
  return request(`${V2_BASE}/cycles?limit=${limit}`);
}

/** Fetch recent decision funnel outcomes, newest first. */
export async function fetchDecisions(limit = 50) {
  return request(`${V2_BASE}/decisions?limit=${limit}`);
}

/** Fetch exchange truth + local projection side-by-side. */
export async function fetchPositions() {
  return request(`${V2_BASE}/positions`);
}

/** Fetch latest reconciliation result. */
export async function fetchReconciliation() {
  return request(`${V2_BASE}/reconciliation`);
}

/** Fetch recent recovery incidents, newest first. */
export async function fetchIncidents(limit = 20) {
  return request(`${V2_BASE}/incidents?limit=${limit}`);
}

/** Fetch recent LLM invocation records (includes skips and errors). */
export async function fetchLLMInvocations(limit = 20) {
  return request(`${V2_BASE}/llm-invocations?limit=${limit}`);
}

/** Fetch the latest evidence bundle from the last complete cycle. */
export async function fetchLatestEvidence() {
  return request(`${V2_BASE}/evidence/latest`);
}

/** Disable new entries (reduce-only exits are never affected). */
export async function disableEntry(reason) {
  return request(`${V2_BASE}/controls/entry-disable`, {
    method: "POST",
    body: JSON.stringify({ reason }),
  });
}

/** Re-enable entries after a manual disable. */
export async function enableEntry(reason) {
  return request(`${V2_BASE}/controls/entry-enable`, {
    method: "POST",
    body: JSON.stringify({ reason }),
  });
}
