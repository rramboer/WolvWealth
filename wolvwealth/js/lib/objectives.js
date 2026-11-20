// Optimization objectives offered by the API, described for people rather
// than quants. Volatility and return targets are entered in percent and sent
// to the API as annual decimals (15 -> 0.15).

export const RISK_FREE_RATE_PERCENT = 2; // What cash earns on the server (DEFAULT_RISK_FREE_RATE).

export const OBJECTIVES = [
  {
    value: "max_sharpe",
    label: "Best return for the risk (max Sharpe)",
    description: `The most expected return per unit of volatility. Holds cash if no stock is expected to beat the ${RISK_FREE_RATE_PERCENT}% that cash earns.`,
  },
  {
    value: "min_volatility",
    label: "Smallest swings (min volatility)",
    description: "The calmest fully-invested portfolio, whatever its expected return.",
  },
  {
    value: "efficient_risk",
    label: "Most return within a volatility limit",
    description: "The highest expected return whose volatility stays within your limit.",
    param: {
      key: "targetVolatility",
      api: "target_volatility",
      label: "Volatility limit (% a year)",
      hint: "Roughly how much the portfolio may swing in a typical year. The stock market as a whole is often 15–20%.",
      percent: true,
      min: 0,
      exclusiveMin: true,
      max: 500,
      step: 1,
      default: 15,
    },
  },
  {
    value: "efficient_return",
    label: "Steadiest path to a return target",
    description: "The calmest portfolio expected to reach your return target.",
    param: {
      key: "targetReturn",
      api: "target_return",
      label: "Return target (% a year)",
      hint: `At or below the ${RISK_FREE_RATE_PERCENT}% cash rate, the answer is to hold cash.`,
      percent: true,
      min: -100,
      max: 500,
      step: 1,
      default: 10,
    },
  },
  {
    value: "max_utility",
    label: "Balance return against risk",
    description: `Weighs expected return against volatility by how cautious you are. Holds cash if no portfolio's return after the risk penalty beats the ${RISK_FREE_RATE_PERCENT}% cash rate.`,
    param: {
      key: "riskAversion",
      api: "risk_aversion",
      label: "Risk aversion",
      hint: "Higher is more cautious. Low values (1–5) concentrate in a few high-return stocks; 10–30 spread out; very high values may recommend cash.",
      percent: false,
      min: 0,
      exclusiveMin: true,
      max: 1000,
      step: 1,
      default: 10,
    },
  },
];

export const DEFAULT_OBJECTIVE_FORM = {
  objective: "max_sharpe",
  ...Object.fromEntries(OBJECTIVES.filter((o) => o.param).map((o) => [o.param.key, o.param.default])),
};

export function findObjective(value) {
  return OBJECTIVES.find((objective) => objective.value === value) ?? OBJECTIVES[0];
}

/** The API's `objective` object for the form: its name plus the one parameter it takes. */
export function buildObjectivePayload(form) {
  const objective = findObjective(form.objective);
  const payload = { name: objective.value };
  if (objective.param) {
    const value = form[objective.param.key];
    // toPrecision trims float noise such as 14.3 / 100 = 0.14300000000000002.
    payload[objective.param.api] = objective.param.percent ? Number((value / 100).toPrecision(12)) : value;
  }
  return payload;
}

/** "Most return within a volatility limit (15%)" for a response's echoed objective. */
export function describeObjective(apiObjective) {
  if (!apiObjective) return null;
  const objective = findObjective(apiObjective.name);
  if (!objective.param) return objective.label;
  const raw = apiObjective[objective.param.api];
  if (typeof raw !== "number") return objective.label;
  const shown = objective.param.percent ? `${Number((raw * 100).toPrecision(12))}%` : `${raw}`;
  return `${objective.label} (${objective.param.percent ? "" : "risk aversion "}${shown})`;
}
