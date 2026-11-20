// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import OptimizerPage from "../pages/optimizer";

const config = {
  csrfToken: "tok",
  firstPriceDate: "2006-01-03",
  lastPriceDate: "2024-01-30",
  universeSize: 1981,
  usesRemaining: 15,
  maxRebalances: 120,
  maxUniverseSize: 100,
};

/** jsdom does not submit a form when its submit button is clicked, so submit the form directly. */
function submitForm(buttonName) {
  fireEvent.submit(screen.getByRole("button", { name: buttonName }).closest("form"));
}

function jsonResponse(status, body) {
  return { ok: status < 300, status, json: () => Promise.resolve(body) };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("OptimizerPage", () => {
  it("renders both tabs with the optimizer shown first", () => {
    render(<OptimizerPage config={config} />);
    expect(screen.getByRole("tab", { name: "Optimize" }).getAttribute("aria-selected")).toBe("true");
    expect(screen.getByText("15")).toBeTruthy();
    expect(screen.getByText("Portfolio settings")).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "Backtest" }));
    expect(screen.getByRole("tab", { name: "Backtest" }).getAttribute("aria-selected")).toBe("true");
    expect(screen.getByText("Backtest settings")).toBeTruthy();
  });

  it("defaults the backtest to the last three years of data and estimates the schedule", () => {
    render(<OptimizerPage config={config} />);
    fireEvent.click(screen.getByRole("tab", { name: "Backtest" }));
    expect(screen.getByLabelText("Start").value).toBe("2021-01-30");
    expect(screen.getByLabelText("End").value).toBe("2024-01-30");
    expect(screen.getByText(/≈ 37 rebalances/)).toBeTruthy();
  });

  it("warns and disables the run button when the schedule exceeds the limit", () => {
    render(<OptimizerPage config={config} />);
    fireEvent.click(screen.getByRole("tab", { name: "Backtest" }));
    fireEvent.change(screen.getByLabelText("Rebalance frequency"), { target: { value: "daily" } });
    expect(screen.getByRole("status").textContent).toMatch(/exceeds the limit/);
    expect(screen.getByRole("button", { name: "Run backtest" }).disabled).toBe(true);
  });

  it("blocks submission and explains when a number is invalid", () => {
    render(<OptimizerPage config={config} />);
    fireEvent.change(screen.getByLabelText("Amount to invest ($)"), { target: { value: "-5" } });
    expect(screen.getByRole("button", { name: "Optimize" }).disabled).toBe(true);
    expect(screen.getByRole("note").textContent).toBe("Must be greater than 0.");
    fireEvent.change(screen.getByLabelText("Amount to invest ($)"), { target: { value: "" } });
    expect(screen.getByRole("note").textContent).toBe("Required.");
    fireEvent.change(screen.getByLabelText("Amount to invest ($)"), { target: { value: "5000" } });
    expect(screen.getByRole("button", { name: "Optimize" }).disabled).toBe(false);
  });

  it("runs an optimization and shows the allocation, decrementing uses", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        metrics: { expected_annual_return: 0.2, annual_volatility: 0.1, sharpe_ratio: 1.8, portfolio_value: 10000 },
        optimized_portfolio: {
          AAPL: { percent_weight: 60, value: 6000, shares: 30 },
          MSFT: { percent_weight: 40, value: 4000, shares: 10 },
        },
      }),
    );
    render(<OptimizerPage config={config} />);
    submitForm("Optimize");
    await waitFor(() => expect(screen.getByRole("cell", { name: "AAPL" })).toBeTruthy());
    expect(screen.getByText("60.00%")).toBeTruthy();
    expect(screen.getByText("14")).toBeTruthy(); // uses remaining went down by one
    const [, init] = globalThis.fetch.mock.calls[0];
    expect(JSON.parse(init.body)).toEqual({
      initial_cash: 10000,
      universe: ["TOP50"],
      objective: { name: "max_sharpe" },
      constraints: { max_positions: 10 },
    });
  });

  it("optimizes for a volatility limit entered in percent, and says what it optimized for", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        metrics: { expected_annual_return: 0.3, annual_volatility: 0.12, sharpe_ratio: 2.3, portfolio_value: 10000 },
        optimized_portfolio: { AAPL: { percent_weight: 100, value: 10000, shares: 50 } },
        objective: { name: "efficient_risk", target_volatility: 0.12 },
      }),
    );
    render(<OptimizerPage config={config} />);
    expect(screen.queryByLabelText("Volatility limit (% a year)")).toBeNull();
    const form = within(screen.getByRole("button", { name: "Optimize" }).closest("form"));
    fireEvent.change(form.getByLabelText("Optimize for"), { target: { value: "efficient_risk" } });
    expect(screen.getByText(/highest expected return whose volatility stays within your limit/)).toBeTruthy();
    const limit = screen.getByLabelText("Volatility limit (% a year)");
    expect(limit.value).toBe("15");

    fireEvent.change(limit, { target: { value: "0" } });
    expect(screen.getByRole("note").textContent).toBe("Must be greater than 0.");
    expect(screen.getByRole("button", { name: "Optimize" }).disabled).toBe(true);

    fireEvent.change(limit, { target: { value: "12" } });
    submitForm("Optimize");
    await waitFor(() => expect(screen.getByRole("cell", { name: "AAPL" })).toBeTruthy());
    const [, init] = globalThis.fetch.mock.calls[0];
    expect(JSON.parse(init.body).objective).toEqual({ name: "efficient_risk", target_volatility: 0.12 });
    expect(screen.getByText("Optimized for: Most return within a volatility limit (12%).")).toBeTruthy();
  });

  it("shows API errors from the optimizer", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(jsonResponse(400, { error: { message: "Invalid symbol in universe: ZZZ." } }));
    render(<OptimizerPage config={config} />);
    submitForm("Optimize");
    await waitFor(() => expect(screen.getByRole("alert").textContent).toBe("Invalid symbol in universe: ZZZ."));
  });
});
