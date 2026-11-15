// Pure transformations of a backtest report into chart-ready rows, plus the
// small bits of form arithmetic that are worth unit-testing.

export const OPTIMIZER_KEY = "optimizer";

/** Series keys in display order: the optimizer first, then benchmarks as the API ordered them. */
export function seriesKeys(report) {
  return [OPTIMIZER_KEY, ...Object.keys(report.benchmarks ?? {})];
}

function curves(report) {
  const out = { [OPTIMIZER_KEY]: report.result.equity ?? {} };
  for (const [name, benchmark] of Object.entries(report.benchmarks ?? {})) {
    out[name] = benchmark.result.equity ?? {};
  }
  return out;
}

/** One row per trading day: { date, optimizer: value, equal_weight: value, ... }. */
export function equityRows(report) {
  const series = curves(report);
  const dates = Object.keys(series[OPTIMIZER_KEY]).sort();
  return dates.map((date) => {
    const row = { date };
    for (const [key, curve] of Object.entries(series)) {
      if (curve[date] !== undefined) row[key] = curve[date];
    }
    return row;
  });
}

/** Same shape as equityRows but each value is the drawdown from the running peak (0 or negative). */
export function drawdownRows(report) {
  const rows = equityRows(report);
  const peaks = {};
  return rows.map((row) => {
    const out = { date: row.date };
    for (const [key, value] of Object.entries(row)) {
      if (key === "date") continue;
      peaks[key] = Math.max(peaks[key] ?? -Infinity, value);
      out[key] = value / peaks[key] - 1;
    }
    return out;
  });
}

/** Split free text into universe entries: "AAPL, msft top10" -> ["AAPL", "MSFT", "TOP10"]. */
export function parseUniverse(text) {
  return [...new Set(text.split(/[\s,;]+/).filter(Boolean).map((entry) => entry.toUpperCase()))];
}

const AVERAGE_DAYS = {
  daily: 1,
  weekly: 7,
  biweekly: 14,
  monthly: 365.25 / 12,
  quarterly: 365.25 / 4,
  semiannually: 365.25 / 2,
  yearly: 365.25,
};

/**
 * Approximate rebalance count for a date range so the form can warn before
 * the server does. The schedule includes the start date, hence the +1.
 * Daily counts trading days (5/7 of calendar days).
 */
export function estimateRebalances(start, end, frequency) {
  const startDate = new Date(start);
  const endDate = new Date(end);
  if (Number.isNaN(startDate.getTime()) || Number.isNaN(endDate.getTime()) || endDate <= startDate) return 0;
  let days = (endDate - startDate) / 86_400_000;
  if (frequency === "daily") days *= 5 / 7;
  return Math.round(days / AVERAGE_DAYS[frequency]) + 1;
}

/** Rough wall-clock estimate: the optimizer costs about 0.7 s per rebalance for 50 stocks. */
export function estimateSeconds(rebalances, universeSize) {
  const perRebalance = 0.7 * Math.max(0.3, universeSize / 50);
  return Math.round(rebalances * perRebalance);
}
