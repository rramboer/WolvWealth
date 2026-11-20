// Client-side validation mirroring the server's rules, so bad input is caught
// before a request (and a use) is spent. The server remains the authority.
import { hasHoldings, parseHoldings } from "./holdings";
import { findObjective } from "./objectives";
import { parseUniverse } from "./series";

export const LIMITS = {
  maxCash: 1e12,
  minLookbackDays: 30,
  maxLookbackDays: 15 * 365,
  maxExecutionLag: 30,
  maxCostBps: 1000,
};

const TICKER = /^[A-Z][A-Z0-9.-]{0,9}$/;
const TOP_N = /^TOP\d+$/;

const isFinite = (value) => typeof value === "number" && Number.isFinite(value);
const isInteger = (value) => Number.isInteger(value);

function numberError(value, { min, max, exclusiveMin = false, integer = false } = {}) {
  if (value === "" || value === null || value === undefined) return "Required.";
  if (typeof value !== "number" || Number.isNaN(value)) return "Must be a finite number.";
  // A typed 1 followed by hundreds of zeros parses to Infinity in the browser.
  if (!isFinite(value)) return "Out of range.";
  if (integer && !isInteger(value)) return "Must be a whole number.";
  if (min !== undefined && (value < min || (exclusiveMin && value === min))) {
    return exclusiveMin ? `Must be greater than ${min}.` : `Must be at least ${min}.`;
  }
  if (max !== undefined && value > max) return `Must be at most ${max.toLocaleString("en-US")}.`;
  return null;
}

/** How many tickers the universe text expands to ("top50" counts as 50, capped at the data's size). */
export function universeCount(text, universeSize) {
  let count = 0;
  for (const entry of parseUniverse(text ?? "")) {
    count += TOP_N.test(entry) ? Math.min(Number(entry.slice(3)), universeSize ?? Infinity) : 1;
  }
  return count;
}

function universeError(text, maxUniverseSize, universeSize) {
  const entries = parseUniverse(text ?? "");
  if (!entries.length) return "Enter at least one ticker or topN.";
  const bad = entries.filter((entry) => !TICKER.test(entry) && !TOP_N.test(entry));
  if (bad.length) return `Not a ticker: ${bad.slice(0, 3).join(", ")}.`;
  if (maxUniverseSize && universeCount(text, universeSize) > maxUniverseSize) return `At most ${maxUniverseSize} tickers.`;
  return null;
}

/**
 * Mirrors the server's feasibility rule: the portfolio holds at most
 * `positions` stocks (the universe, or fewer if max positions caps it), each at
 * most max weight, so positions x max weight must reach 100%.
 */
function constraintErrors(form, stocks) {
  const errors = {};
  const maxPositions = numberError(form.maxPositions, { min: 0, integer: true });
  if (maxPositions) errors.maxPositions = maxPositions;
  const maxWeight = numberError(form.maxWeight, { min: 0, max: 1, exclusiveMin: true });
  if (maxWeight) errors.maxWeight = maxWeight;
  const minWeight = numberError(form.minWeight, { min: 0, max: 1 });
  if (minWeight) errors.minWeight = minWeight;
  if (!maxWeight && !minWeight && form.minWeight > form.maxWeight) {
    errors.minWeight = "Must not exceed max weight.";
  }
  if (!maxPositions && !maxWeight && stocks > 0) {
    const capped = form.maxPositions > 0 && form.maxPositions < stocks;
    const positions = capped ? form.maxPositions : stocks;
    if (positions * form.maxWeight < 1 - 1e-9) {
      const reach = `${Math.round(positions * form.maxWeight * 100)}%`;
      if (capped) {
        errors.maxPositions = `${positions} positions × max weight only reaches ${reach}; needs 100%.`;
      } else {
        const need = (Math.ceil((1 / positions) * 100) / 100).toFixed(2);
        errors.maxWeight = `${positions} stocks × ${form.maxWeight} only reaches ${reach}; use at least ${need}.`;
      }
    }
  }
  if (!minWeight && stocks > 0 && form.minWeight * stocks > 1 + 1e-9) {
    errors.minWeight = `${stocks} stocks × ${form.minWeight} exceeds 100%.`;
  }
  return errors;
}

/** Optional max turnover, in percent of the portfolio sold: empty means no limit. */
function maxTurnoverErrors(form) {
  if (form.maxTurnover === "" || form.maxTurnover === undefined || form.maxTurnover === null) return {};
  const error = numberError(form.maxTurnover, { min: 0, max: 100 });
  return error ? { maxTurnover: error } : {};
}

/** The selected objective's parameter, if it takes one, must be present and in the server's range. */
function objectiveErrors(form) {
  const { param } = findObjective(form.objective);
  if (!param) return {};
  const error = numberError(form[param.key], { min: param.min, max: param.max, exclusiveMin: param.exclusiveMin });
  return error ? { [param.key]: error } : {};
}

function dateError(value) {
  if (!value) return "Required.";
  return Number.isNaN(new Date(value).getTime()) ? "Invalid date." : null;
}

/** Errors keyed by form field; empty object means the form may be submitted. */
export function validateBacktestForm(form, config = {}) {
  const errors = {
    ...constraintErrors(form, universeCount(form.universe, config.universeSize)),
    ...objectiveErrors(form),
    ...maxTurnoverErrors(form),
  };
  const start = dateError(form.start);
  const end = dateError(form.end);
  if (start) errors.start = start;
  if (end) errors.end = end;
  if (!start && !end) {
    if (form.start >= form.end) errors.end = "Must be after start.";
    if (config.firstPriceDate && form.start < config.firstPriceDate) errors.start = `No prices before ${config.firstPriceDate}.`;
    if (config.lastPriceDate && form.end > config.lastPriceDate) errors.end = `No prices after ${config.lastPriceDate}.`;
  }
  const cash = numberError(form.initialCash, { min: 0, max: LIMITS.maxCash, exclusiveMin: true });
  if (cash) errors.initialCash = cash;
  const lookback = numberError(form.lookbackDays, {
    min: LIMITS.minLookbackDays,
    max: LIMITS.maxLookbackDays,
    integer: true,
  });
  if (lookback) errors.lookbackDays = lookback;
  const lag = numberError(form.executionLag, { min: 0, max: LIMITS.maxExecutionLag, integer: true });
  if (lag) errors.executionLag = lag;
  const cost = numberError(form.costBps, { min: 0, max: LIMITS.maxCostBps });
  if (cost) errors.costBps = cost;
  const universe = universeError(form.universe, config.maxUniverseSize, config.universeSize);
  if (universe) errors.universe = universe;
  return errors;
}

export function validateOptimizeForm(form, config = {}) {
  const errors = {
    ...constraintErrors(form, universeCount(form.universe, config.universeSize)),
    ...objectiveErrors(form),
    ...maxTurnoverErrors(form),
  };
  const holdings = parseHoldings(form.holdings).error;
  if (holdings) errors.holdings = holdings;
  // With holdings, the cash to add may be 0: the portfolio is what is already held.
  const cashRule = !holdings && hasHoldings(form.holdings) ? {} : { exclusiveMin: true };
  const cash = numberError(form.initialCash, { min: 0, max: LIMITS.maxCash, ...cashRule });
  if (cash) errors.initialCash = cash;
  if (form.costBps !== undefined) {
    const cost = numberError(form.costBps, { min: 0, max: LIMITS.maxCostBps });
    if (cost) errors.costBps = cost;
  }
  const universe = universeError(form.universe, undefined, config.universeSize);
  if (universe) errors.universe = universe;
  return errors;
}

export const isValid = (errors) => Object.keys(errors).length === 0;
