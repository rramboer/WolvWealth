// Client-side validation mirroring the server's rules, so bad input is caught
// before a request (and a use) is spent. The server remains the authority.
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
  if (!isFinite(value)) return "Must be a finite number.";
  if (integer && !isInteger(value)) return "Must be a whole number.";
  if (min !== undefined && (value < min || (exclusiveMin && value === min))) {
    return exclusiveMin ? `Must be greater than ${min}.` : `Must be at least ${min}.`;
  }
  if (max !== undefined && value > max) return `Must be at most ${max.toLocaleString("en-US")}.`;
  return null;
}

function universeError(text, maxUniverseSize, universeSize) {
  const entries = parseUniverse(text ?? "");
  if (!entries.length) return "Enter at least one ticker or topN.";
  const bad = entries.filter((entry) => !TICKER.test(entry) && !TOP_N.test(entry));
  if (bad.length) return `Not a ticker: ${bad.slice(0, 3).join(", ")}.`;
  let count = 0;
  for (const entry of entries) {
    const top = TOP_N.exec(entry);
    count += top ? Math.min(Number(entry.slice(3)), universeSize ?? Infinity) : 1;
  }
  if (maxUniverseSize && count > maxUniverseSize) return `At most ${maxUniverseSize} tickers.`;
  return null;
}

function constraintErrors(form) {
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
  if (!maxPositions && !maxWeight && form.maxPositions > 0 && form.maxPositions * form.maxWeight < 1) {
    errors.maxPositions = "Positions × max weight must reach 100%.";
  }
  return errors;
}

function dateError(value) {
  if (!value) return "Required.";
  return Number.isNaN(new Date(value).getTime()) ? "Invalid date." : null;
}

/** Errors keyed by form field; empty object means the form may be submitted. */
export function validateBacktestForm(form, config = {}) {
  const errors = { ...constraintErrors(form) };
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
  const errors = { ...constraintErrors(form) };
  const cash = numberError(form.initialCash, { min: 0, max: LIMITS.maxCash, exclusiveMin: true });
  if (cash) errors.initialCash = cash;
  const universe = universeError(form.universe, undefined, config.universeSize);
  if (universe) errors.universe = universe;
  return errors;
}

export const isValid = (errors) => Object.keys(errors).length === 0;
