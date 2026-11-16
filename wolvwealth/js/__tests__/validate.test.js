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
    expect(validateBacktestForm({ ...good, maxPositions: 2, maxWeight: 0.2 }, config).maxPositions).toMatch(/100%/);
    expect(validateBacktestForm({ ...good, maxPositions: 2.5 }, config).maxPositions).toMatch(/whole number/);
    expect(validateBacktestForm({ ...good, maxPositions: 0 }, config).maxPositions).toBeUndefined();
  });
});

describe("validateOptimizeForm", () => {
  it("covers cash, universe and constraints", () => {
    expect(validateOptimizeForm({ initialCash: 10000, universe: "top50", maxPositions: 10, maxWeight: 1, minWeight: 0 })).toEqual({});
    const errors = validateOptimizeForm({ initialCash: NaN, universe: "", maxPositions: -1, maxWeight: 1, minWeight: 0 });
    expect(Object.keys(errors).sort()).toEqual(["initialCash", "maxPositions", "universe"]);
  });
});
