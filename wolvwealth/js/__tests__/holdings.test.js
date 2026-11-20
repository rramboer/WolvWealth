import { describe, expect, it } from "vitest";
import { buildBacktestPayload } from "../features/BacktestTab";
import { buildOptimizePayload } from "../features/OptimizeTab";
import { hasHoldings, parseHoldings } from "../lib/holdings";

describe("parseHoldings", () => {
  it("reads lines, commas, colons and equals signs, case-insensitively", () => {
    expect(parseHoldings("AAPL 10\nmsft: 2.5, brk-b=3")).toEqual({
      holdings: { AAPL: 10, MSFT: 2.5, "BRK-B": 3 },
      error: null,
    });
  });

  it("adds up repeated tickers and ignores blank entries", () => {
    expect(parseHoldings("AAPL 1\n\nAAPL 2,,").holdings).toEqual({ AAPL: 3 });
    expect(parseHoldings("")).toEqual({ holdings: {}, error: null });
  });

  it.each([
    ["AAPL", /ticker and a share count/], // Regression: parsed as AAP with "L" shares.
    ["AAPL10", /ticker and a share count/],
    ["AAPL ten", /shares must be a number/],
    ["AAPL -1", /shares must be a number/],
    ["AAPL 1e13", /shares must be a number/],
    ["1AAPL 3", /ticker and a share count/],
    ["TOOLONGTICKER 3", /Not a ticker/],
  ])("rejects %p", (text, message) => {
    const { holdings, error } = parseHoldings(text);
    expect(holdings).toEqual({});
    expect(error).toMatch(message);
  });

  it("knows whether anything is held", () => {
    expect(hasHoldings("AAPL 0")).toBe(false);
    expect(hasHoldings("AAPL 0, MSFT 1")).toBe(true);
    expect(hasHoldings("garbage")).toBe(false);
  });
});

describe("payloads", () => {
  const optimizeForm = {
    initialCash: 0,
    holdings: "AAPL 10",
    universe: "top50",
    objective: "max_sharpe",
    maxPositions: 10,
    maxWeight: 1,
    minWeight: 0,
    maxTurnover: 15,
    costBps: 5,
  };

  it("sends holdings, the turnover limit as a fraction, and the trading cost", () => {
    expect(buildOptimizePayload(optimizeForm)).toEqual({
      initial_cash: 0,
      universe: ["TOP50"],
      objective: { name: "max_sharpe" },
      constraints: { max_positions: 10, max_turnover: 0.15 },
      initial_holdings: { AAPL: 10 },
      transaction_cost_bps: 5,
    });
  });

  it("omits what is not set, and keeps 0% turnover as a limit", () => {
    const payload = buildOptimizePayload({ ...optimizeForm, holdings: "", maxTurnover: "", costBps: 0 });
    expect(payload).not.toHaveProperty("initial_holdings");
    expect(payload).not.toHaveProperty("transaction_cost_bps");
    expect(payload.constraints).not.toHaveProperty("max_turnover");
    expect(buildOptimizePayload({ ...optimizeForm, maxTurnover: 0 }).constraints.max_turnover).toBe(0);
  });

  it("sends a backtest's turnover limit as a fraction", () => {
    const form = { start: "2021-01-01", end: "2024-01-01", universe: "top5", maxPositions: 0, maxWeight: 1, minWeight: 0 };
    expect(buildBacktestPayload({ ...form, maxTurnover: 12.5 }).constraints).toEqual({ max_turnover: 0.125 });
    expect(buildBacktestPayload({ ...form, maxTurnover: "" }).constraints).toEqual({});
  });
});
