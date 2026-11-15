import { describe, expect, it } from "vitest";
import { drawdownRows, equityRows, estimateRebalances, parseUniverse, seriesKeys } from "../lib/series";

const report = {
  result: { equity: { "2024-01-03": 110, "2024-01-01": 100, "2024-01-02": 120 } },
  benchmarks: {
    equal_weight: { result: { equity: { "2024-01-01": 100, "2024-01-02": 90, "2024-01-03": 95 } } },
    SPY: { result: { equity: { "2024-01-01": 100, "2024-01-03": 101 } } }, // missing a day
  },
};

describe("seriesKeys", () => {
  it("puts the optimizer first and keeps benchmark order", () => {
    expect(seriesKeys(report)).toEqual(["optimizer", "equal_weight", "SPY"]);
  });
});

describe("equityRows", () => {
  it("merges curves by sorted date and leaves gaps undefined", () => {
    const rows = equityRows(report);
    expect(rows.map((row) => row.date)).toEqual(["2024-01-01", "2024-01-02", "2024-01-03"]);
    expect(rows[1]).toEqual({ date: "2024-01-02", optimizer: 120, equal_weight: 90 });
    expect(rows[2].SPY).toBe(101);
  });
});

describe("drawdownRows", () => {
  it("measures the fall from the running peak per series", () => {
    const rows = drawdownRows(report);
    expect(rows[0].optimizer).toBe(0);
    expect(rows[2].optimizer).toBeCloseTo(110 / 120 - 1);
    expect(rows[1].equal_weight).toBeCloseTo(-0.1);
    expect(rows[2].equal_weight).toBeCloseTo(-0.05);
  });
});

describe("parseUniverse", () => {
  it("splits on whitespace/commas, upper-cases and de-duplicates", () => {
    expect(parseUniverse(" aapl, MSFT top10;aapl ")).toEqual(["AAPL", "MSFT", "TOP10"]);
    expect(parseUniverse("")).toEqual([]);
  });
});

describe("estimateRebalances", () => {
  it("approximates the schedule length", () => {
    // Jan 2023 through Jan 2024 inclusive: 13 monthly decisions, 5 quarterly.
    expect(estimateRebalances("2023-01-01", "2024-01-01", "monthly")).toBe(13);
    expect(estimateRebalances("2023-01-01", "2024-01-01", "quarterly")).toBe(5);
    expect(estimateRebalances("2023-01-01", "2023-01-15", "weekly")).toBe(3);
    expect(estimateRebalances("2021-01-30", "2024-01-30", "monthly")).toBe(37);
  });
  it("is zero for an empty or inverted range", () => {
    expect(estimateRebalances("2024-01-01", "2023-01-01", "monthly")).toBe(0);
    expect(estimateRebalances("", "2023-01-01", "monthly")).toBe(0);
  });
});
