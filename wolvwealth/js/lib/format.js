// Display formatting. All functions accept null/undefined and return "n/a".

const NA = "n/a";

export function formatPercent(value, { signed = true, digits = 2 } = {}) {
  if (value === null || value === undefined || Number.isNaN(value)) return NA;
  const text = `${(value * 100).toFixed(digits)}%`;
  return signed && value > 0 ? `+${text}` : text;
}

export function formatMoney(value, { digits = 2 } = {}) {
  if (value === null || value === undefined || Number.isNaN(value)) return NA;
  return value.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function formatNumber(value, { digits = 2 } = {}) {
  if (value === null || value === undefined || Number.isNaN(value)) return NA;
  return value.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function formatSeconds(seconds) {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return NA;
  const whole = Math.max(0, Math.round(seconds));
  if (whole < 60) return `${whole}s`;
  return `${Math.floor(whole / 60)}m ${String(whole % 60).padStart(2, "0")}s`;
}

/** Human label for a series key: "equal_weight" -> "Equal weight", "SPY" stays "SPY". */
export function seriesLabel(key) {
  if (key === "optimizer") return "Optimizer";
  if (key === "equal_weight") return "Equal weight";
  return key;
}
