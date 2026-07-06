const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "";
const ADMIN_API_TOKEN = import.meta.env.VITE_ADMIN_API_TOKEN ?? "dev-admin-token";

export function apiUrl(path) {
  return `${API_BASE}${path}`;
}

export async function request(path, options = {}) {
  const response = await fetch(apiUrl(path), {
    headers: {
      "Content-Type": "application/json",
      ...(ADMIN_API_TOKEN ? { Authorization: `Bearer ${ADMIN_API_TOKEN}` } : {}),
      ...(options.headers ?? {}),
    },
    ...options,
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const message = payload?.message ?? payload?.detail ?? response.statusText;
    throw new Error(typeof message === "string" ? message : "请求失败");
  }
  return payload;
}
