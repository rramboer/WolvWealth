import { describe, expect, it } from "vitest";
import {
  DEFAULT_OBJECTIVE_FORM,
  OBJECTIVES,
  buildObjectivePayload,
  describeObjective,
  findObjective,
} from "../lib/objectives";

describe("buildObjectivePayload", () => {
  it("sends only the name for parameterless objectives", () => {
    expect(buildObjectivePayload({ ...DEFAULT_OBJECTIVE_FORM })).toEqual({ name: "max_sharpe" });
    expect(buildObjectivePayload({ ...DEFAULT_OBJECTIVE_FORM, objective: "min_volatility" })).toEqual({
      name: "min_volatility",
    });
  });

  it("converts percent targets to annual decimals, without float noise", () => {
    const form = { ...DEFAULT_OBJECTIVE_FORM, objective: "efficient_risk", targetVolatility: 14.3 };
    expect(buildObjectivePayload(form)).toEqual({ name: "efficient_risk", target_volatility: 0.143 });
    expect(buildObjectivePayload({ ...form, objective: "efficient_return", targetReturn: 7 })).toEqual({
      name: "efficient_return",
      target_return: 0.07,
    });
  });

  it("sends risk aversion as entered, and never another objective's parameter", () => {
    const form = { ...DEFAULT_OBJECTIVE_FORM, objective: "max_utility", riskAversion: 12 };
    expect(buildObjectivePayload(form)).toEqual({ name: "max_utility", risk_aversion: 12 });
  });

  it("falls back to max Sharpe for an unknown name", () => {
    expect(findObjective("nope").value).toBe("max_sharpe");
  });
});

describe("defaults", () => {
  it("cover every objective that takes a parameter, within its range", () => {
    for (const { param } of OBJECTIVES.filter((o) => o.param)) {
      const value = DEFAULT_OBJECTIVE_FORM[param.key];
      expect(value).toBeGreaterThan(param.min);
      expect(value).toBeLessThanOrEqual(param.max);
    }
  });
});

describe("describeObjective", () => {
  it("labels an echoed objective with its parameter", () => {
    expect(describeObjective({ name: "max_sharpe" })).toBe("Best return for the risk (max Sharpe)");
    expect(describeObjective({ name: "efficient_return", target_return: 0.143 })).toBe(
      "Steadiest path to a return target (14.3%)",
    );
    expect(describeObjective({ name: "max_utility", risk_aversion: 10 })).toBe(
      "Balance return against risk (risk aversion 10)",
    );
  });

  it("is null when the response has no objective (older servers)", () => {
    expect(describeObjective(undefined)).toBeNull();
  });
});
