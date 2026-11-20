import { describe, expect, it } from "vitest";
import { isValid, validateBacktestForm, validateOptimizeForm } from "../lib/validate";

const config = { firstPriceDate: "2006-01-03", lastPriceDate: "2024-01-30", universeSize: 1981, maxUniverseSize: 100 };

const good = {
  start: "2021-01-30",
  end: "2024-01-30",
  initialCash: 100000,
  universe: "top50",
  lookbackDays: 1095,
  executionLag: 1,
  costBps: 5,
  maxPositions: 10,
  maxWeight: 1,
  minWeight: 0,
};

describe("validateBacktestForm", () => {
  it("accepts the defaults", () => {
    expect(validateBacktestForm(good, config)).toEqual({});
    expect(isValid({})).toBe(true);
  });

  it.each([NaN, Infinity, -Infinity, "", null, 0, -1, 1e13])("rejects initial cash %p", (value) => {
    expect(validateBacktestForm({ ...good, initialCash: value }, config).initialCash).toBeTruthy();
  });

  it("requires whole numbers within range for lookback and lag", () => {
    expect(validateBacktestForm({ ...good, lookbackDays: 10 }, config).lookbackDays).toMatch(/at least 30/);
    expect(validateBacktestForm({ ...good, lookbackDays: 100.5 }, config).lookbackDays).toMatch(/whole number/);
    expect(validateBacktestForm({ ...good, lookbackDays: 1e9 }, config).lookbackDays).toMatch(/at most/);
    expect(validateBacktestForm({ ...good, executionLag: 31 }, config).executionLag).toMatch(/at most 30/);
    expect(validateBacktestForm({ ...good, executionLag: -1 }, config).executionLag).toMatch(/at least 0/);
  });

  it("calls a typed number too big for the browser out of range (regression)", () => {
    // Number("1" + "0".repeat(400)) is Infinity.
    const huge = Number("1" + "0".repeat(400));
    expect(validateBacktestForm({ ...good, initialCash: huge }, config).initialCash).toBe("Out of range.");
    expect(validateBacktestForm({ ...good, lookbackDays: huge }, config).lookbackDays).toBe("Out of range.");
    expect(validateBacktestForm({ ...good, initialCash: -huge }, config).initialCash).toBe("Out of range.");
    expect(validateBacktestForm({ ...good, initialCash: NaN }, config).initialCash).toBe("Must be a finite number.");
  });

  it("bounds transaction costs", () => {
    expect(validateBacktestForm({ ...good, costBps: 1001 }, config).costBps).toBeTruthy();
    expect(validateBacktestForm({ ...good, costBps: NaN }, config).costBps).toMatch(/finite/);
  });

  it("checks the date range against the available prices", () => {
    expect(validateBacktestForm({ ...good, start: "2024-01-30" }, config).end).toMatch(/after start/);
    expect(validateBacktestForm({ ...good, start: "2000-01-01" }, config).start).toMatch(/No prices before/);
    expect(validateBacktestForm({ ...good, end: "2030-01-01" }, config).end).toMatch(/No prices after/);
    expect(validateBacktestForm({ ...good, start: "" }, config).start).toBe("Required.");
    expect(validateBacktestForm({ ...good, start: "not-a-date" }, config).start).toBe("Invalid date.");
  });

  it("validates the universe text", () => {
    expect(validateBacktestForm({ ...good, universe: "" }, config).universe).toBeTruthy();
    expect(validateBacktestForm({ ...good, universe: "AAPL, 123!" }, config).universe).toMatch(/Not a ticker: 123!/);
    expect(validateBacktestForm({ ...good, universe: "top500" }, config).universe).toMatch(/At most 100/);
    expect(validateBacktestForm({ ...good, universe: "aapl msft brk.b" }, config).universe).toBeUndefined();
  });

  it("checks the constraints for feasibility", () => {
    expect(validateBacktestForm({ ...good, maxWeight: 0 }, config).maxWeight).toMatch(/greater than 0/);
    expect(validateBacktestForm({ ...good, maxWeight: 1.5 }, config).maxWeight).toMatch(/at most 1/);
    expect(validateBacktestForm({ ...good, minWeight: 0.5, maxWeight: 0.4 }, config).minWeight).toMatch(/exceed/);
    expect(validateBacktestForm({ ...good, maxPositions: 2, maxWeight: 0.2 }, config).maxPositions).toMatch(/needs 100%/);
    expect(validateBacktestForm({ ...good, maxPositions: 2.5 }, config).maxPositions).toMatch(/whole number/);
    expect(validateBacktestForm({ ...good, maxPositions: 0 }, config).maxPositions).toBeUndefined();
  });
});

describe("feasibility against the universe size", () => {
  it("rejects top5 with max weight 0.1 (regression)", () => {
    const errors = validateBacktestForm({ ...good, universe: "top5", maxPositions: 0, maxWeight: 0.1 }, config);
    expect(errors.maxWeight).toBe("5 stocks × 0.1 only reaches 50%; use at least 0.20.");
  });

  it("rounds the suggested max weight up so the suggestion actually works", () => {
    // 1/3 to nearest cent is 0.33, but 3 x 0.33 = 0.99; the hint must say 0.34.
    const errors = validateBacktestForm({ ...good, universe: "top3", maxPositions: 0, maxWeight: 0.1 }, config);
    expect(errors.maxWeight).toMatch(/use at least 0\.34\./);
    expect(validateBacktestForm({ ...good, universe: "top3", maxPositions: 0, maxWeight: 0.34 }, config)).toEqual({});
    expect(validateBacktestForm({ ...good, universe: "top7", maxPositions: 0, maxWeight: 0.1 }, config).maxWeight).toMatch(
      /use at least 0\.15\./,
    );
  });

  it("counts explicit tickers and accepts the exact boundary", () => {
    expect(validateBacktestForm({ ...good, universe: "AAPL MSFT", maxPositions: 0, maxWeight: 0.4 }, config).maxWeight).toMatch(
      /80%/,
    );
    expect(validateBacktestForm({ ...good, universe: "top5", maxPositions: 0, maxWeight: 0.2 }, config)).toEqual({});
  });

  it("uses the universe when max positions exceeds it", () => {
    const errors = validateBacktestForm({ ...good, universe: "top5", maxPositions: 10, maxWeight: 0.1 }, config);
    expect(errors.maxWeight).toMatch(/5 stocks/);
    expect(errors.maxPositions).toBeUndefined();
  });

  it("rejects a min weight that over-allocates the universe", () => {
    expect(validateBacktestForm({ ...good, universe: "top5", minWeight: 0.25 }, config).minWeight).toMatch(/exceeds 100%/);
  });

  it("applies to the optimize form too", () => {
    const form = { initialCash: 1000, universe: "top5", maxPositions: 0, maxWeight: 0.1, minWeight: 0 };
    expect(validateOptimizeForm(form, config).maxWeight).toMatch(/50%/);
  });
});

describe("validateOptimizeForm", () => {
  it("covers cash, universe and constraints", () => {
    expect(validateOptimizeForm({ initialCash: 10000, universe: "top50", maxPositions: 10, maxWeight: 1, minWeight: 0 })).toEqual({});
    const errors = validateOptimizeForm({ initialCash: NaN, universe: "", maxPositions: -1, maxWeight: 1, minWeight: 0 });
    expect(Object.keys(errors).sort()).toEqual(["initialCash", "maxPositions", "universe"]);
  });
});

describe("objective parameters", () => {
  it("needs no parameter for max Sharpe or min volatility", () => {
    expect(validateOptimizeForm({ ...good, objective: "min_volatility", targetVolatility: "" }, config)).toEqual({});
  });

  it("checks only the selected objective's parameter", () => {
    const form = { ...good, objective: "efficient_return", targetReturn: 10, targetVolatility: -5 };
    expect(validateBacktestForm(form, config)).toEqual({});
  });

  it.each([
    ["efficient_risk", "targetVolatility", 0, /greater than 0/],
    ["efficient_risk", "targetVolatility", 501, /at most 500/],
    ["efficient_risk", "targetVolatility", "", /Required/],
    ["efficient_return", "targetReturn", -101, /at least -100/],
    ["efficient_return", "targetReturn", Infinity, /Out of range/],
    ["max_utility", "riskAversion", 0, /greater than 0/],
    ["max_utility", "riskAversion", 1001, /at most 1,000/],
  ])("%s rejects %s = %p", (objective, key, value, message) => {
    expect(validateOptimizeForm({ ...good, objective, [key]: value }, config)[key]).toMatch(message);
    expect(validateBacktestForm({ ...good, objective, [key]: value }, config)[key]).toMatch(message);
  });

  it("allows a negative return target (the server answers with cash)", () => {
    expect(validateOptimizeForm({ ...good, objective: "efficient_return", targetReturn: -5 }, config)).toEqual({});
  });
});

describe("rebalancing fields", () => {
  const optimize = { initialCash: 1000, universe: "top50", maxPositions: 10, maxWeight: 1, minWeight: 0 };

  it("allows no new cash only when something is held", () => {
    expect(validateOptimizeForm({ ...optimize, initialCash: 0 }, config).initialCash).toMatch(/greater than 0/);
    expect(validateOptimizeForm({ ...optimize, initialCash: 0, holdings: "AAPL 5" }, config)).toEqual({});
    expect(validateOptimizeForm({ ...optimize, initialCash: 0, holdings: "AAPL 0" }, config).initialCash).toBeTruthy();
  });

  it("reports unreadable holdings", () => {
    expect(validateOptimizeForm({ ...optimize, holdings: "AAPL lots" }, config).holdings).toMatch(/AAPL/);
  });

  it.each([-1, 101, Infinity])("rejects max turnover %p", (value) => {
    expect(validateOptimizeForm({ ...optimize, maxTurnover: value }, config).maxTurnover).toBeTruthy();
    expect(validateBacktestForm({ ...good, maxTurnover: value }, config).maxTurnover).toBeTruthy();
  });

  it("treats an empty max turnover as no limit, and 0 as a limit", () => {
    expect(validateOptimizeForm({ ...optimize, maxTurnover: "" }, config)).toEqual({});
    expect(validateOptimizeForm({ ...optimize, maxTurnover: 0 }, config)).toEqual({});
  });

  it("checks the optimize trading cost like the backtest's", () => {
    expect(validateOptimizeForm({ ...optimize, costBps: 1001 }, config).costBps).toMatch(/at most/);
  });
});
