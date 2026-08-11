const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "";
const ADMIN_API_TOKEN = import.meta.env.VITE_ADMIN_API_TOKEN ?? "dev-admin-token";

export function apiUrl(path) {
  return `${API_BASE}${path}`;
}

export function streamUrl(path) {
  const base = API_BASE || window.location.origin;
  const url = new URL(path, base);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  if (ADMIN_API_TOKEN) url.searchParams.set("token", ADMIN_API_TOKEN);
  return url.toString();
}

export async function request(path, options = {}) {
  let response;
  try {
    response = await fetch(apiUrl(path), {
      headers: {
        "Content-Type": "application/json",
        ...(ADMIN_API_TOKEN ? { Authorization: `Bearer ${ADMIN_API_TOKEN}` } : {}),
        ...(options.headers ?? {}),
      },
      ...options,
    });
  } catch (error) {
    if (error?.name === "AbortError") throw error;
    throw new Error("服务暂时不可用，请稍后重试");
  }
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    if (response.status >= 500) {
      throw new Error("服务暂时不可用，请稍后重试");
    }
    const message = payload?.message ?? payload?.detail ?? response.statusText;
    throw new Error(typeof message === "string" ? message : "请求失败");
  }
  return payload;
}
