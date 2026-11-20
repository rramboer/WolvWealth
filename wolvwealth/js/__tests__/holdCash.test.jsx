// @vitest-environment jsdom
// "No asset beats the risk-free rate" is shown as a hold-cash answer, not an error or an empty table.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AllocationTable, RebalanceLog } from "../components/tables";
import OptimizerPage from "../pages/optimizer";

const NOTE =
  "No asset's expected annual return beats the 2.0% risk-free rate (best: AAPL at -5.0%), so the max-Sharpe recommendation is to hold cash.";

const config = {
  csrfToken: "tok",
  firstPriceDate: "2006-01-03",
  lastPriceDate: "2024-01-30",
  universeSize: 1981,
  usesRemaining: 15,
  maxRebalances: 120,
  maxUniverseSize: 100,
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("Optimize tab", () => {
  it("shows the explanation and a cash row instead of an empty allocation", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Headers(),
      json: () =>
        Promise.resolve({
          optimized_portfolio: {},
          cash: { value: 10000, percent_weight: 100 },
          notes: [NOTE],
          metrics: { portfolio_value: 10000, expected_annual_return: 0.02, annual_volatility: 0, sharpe_ratio: 0, num_assets: 0 },
        }),
    });
    render(<OptimizerPage config={config} />);
    fireEvent.submit(screen.getByRole("button", { name: "Optimize" }).closest("form"));
    await waitFor(() => expect(screen.getByRole("cell", { name: "Cash" })).toBeTruthy());
    expect(screen.getByText(NOTE)).toBeTruthy();
    expect(screen.getByText("100.00%")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull(); // an answer, not an error
  });
});

describe("AllocationTable", () => {
  it("omits the cash row when everything is invested", () => {
    render(
      <AllocationTable
        portfolio={{ AAPL: { percent_weight: 100, value: 1000, shares: 5 } }}
        cash={{ value: 0, percent_weight: 0 }}
      />,
    );
    expect(screen.queryByRole("cell", { name: "Cash" })).toBeNull();
    expect(screen.getByRole("cell", { name: "AAPL" })).toBeTruthy();
  });
});

describe("RebalanceLog", () => {
  it("labels a move to cash and shows why", () => {
    const event = (date, weights, note = null) => ({
      decision_date: date,
      execution_date: date,
      portfolio_value: 100000,
      weights,
      turnover: 0,
      transaction_cost: 0,
      note,
    });
    render(
      <RebalanceLog
        result={{
          start_value: 100000,
          rebalances: [event("2022-06-02", {}, NOTE), event("2022-09-02", { NFLX: 1 })],
        }}
      />,
    );
    expect(screen.getByText("Cash 100%")).toBeTruthy();
    expect(screen.getByText(NOTE)).toBeTruthy();
    expect(screen.getByText("NFLX 100%")).toBeTruthy();
  });
});
