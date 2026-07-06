export function formatNumber(value, digits = 2) {
  if (value === null || value === undefined || value === "") return "数据缺失";
  const number = Number(value);
  if (!Number.isFinite(number)) return "数据缺失";
  return new Intl.NumberFormat("zh-CN", {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  }).format(number);
}

export function formatPercent(value) {
  if (value === null || value === undefined || value === "") return "数据缺失";
  const number = Number(value);
  if (!Number.isFinite(number)) return "数据缺失";
  return `${(number * 100).toFixed(4)}%`;
}

export function formatTime(value) {
  if (!value) return "数据缺失";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "数据缺失";
  return date.toLocaleString("zh-CN", { hour12: false });
}

export function asArray(value) {
  return Array.isArray(value) ? value : [];
}
