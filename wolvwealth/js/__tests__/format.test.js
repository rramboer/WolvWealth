import { describe, expect, it } from "vitest";
import { formatMoney, formatPercent, formatSeconds, seriesLabel } from "../lib/format";

describe("formatPercent", () => {
  it("signs gains and keeps losses", () => {
    expect(formatPercent(0.1234)).toBe("+12.34%");
    expect(formatPercent(-0.05)).toBe("-5.00%");
    expect(formatPercent(0.5, { signed: false, digits: 0 })).toBe("50%");
  });
  it("handles missing values", () => {
    expect(formatPercent(null)).toBe("n/a");
    expect(formatPercent(undefined)).toBe("n/a");
  });
});

describe("formatMoney", () => {
  it("formats dollars", () => {
    expect(formatMoney(1234.5)).toBe("$1,234.50");
    expect(formatMoney(1234.5, { digits: 0 })).toBe("$1,235");
  });
});

describe("formatSeconds", () => {
  it("switches to minutes past sixty seconds", () => {
    expect(formatSeconds(12.4)).toBe("12s");
    expect(formatSeconds(75)).toBe("1m 15s");
    expect(formatSeconds(null)).toBe("n/a");
  });
});

describe("seriesLabel", () => {
  it("humanizes known keys and passes tickers through", () => {
    expect(seriesLabel("optimizer")).toBe("Optimizer");
    expect(seriesLabel("equal_weight")).toBe("Equal weight");
    expect(seriesLabel("SPY")).toBe("SPY");
  });
});
