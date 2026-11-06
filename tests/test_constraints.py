"""Constraints: what the solver must honor, and what it must refuse to solve at all.

Organized around two guarantees, because those are what a new constraint has
to preserve:

* **Honored.** ``assert_honors`` re-checks every constraint against the
  returned portfolio, and the feasible grid runs it over combinations. A new
  constraint belongs in that helper and in the grid; the point is that the
  returned plan is checked, not the solver's intermediate answer, since the
  bug this file was written for lived entirely in the step after the solve.
* **Refused.** Every infeasible combination is rejected by name, with the
  fields involved and a remedy, before the solver sees it. The boundary
  tests pin the exact point where feasible becomes infeasible, so a rule
  that is off by one is a failure rather than a rounding opinion.
"""

import numpy as np
import pandas as pd
import pytest

from wolvwealth.domain import constraints, optimizer
from wolvwealth.domain.errors import ValidationError
from wolvwealth.domain.market import MarketData

#: Weights are reported to two decimals of a percent, and a held position can
#: be snapped back up by DUST_WEIGHT (0.01%), so a cap is honored to within a
#: twentieth of a point. The violations this guards against were 2-6 points.
SLACK = 0.05


@pytest.fixture(name="wide")
def wide_market():
    """Twenty tickers: enough for a position cap to actually bind.

    The shared four-ticker market cannot express "cap at 8 of 20", which is
    the shape of the problem, so these tests bring their own.
    """
    rng = np.random.default_rng(11)
    dates = pd.bdate_range("2020-01-02", periods=400)
    tickers = [f"T{i:02d}" for i in range(20)]
    frame = pd.DataFrame(
        {
            ticker: np.round(100 * np.exp(np.cumsum(0.0003 * (i % 6 + 1) + 0.01 * rng.standard_normal(len(dates)))), 2)
            for i, ticker in enumerate(tickers)
        },
        index=dates,
    )
    frame.index.name = "Date"
    return MarketData.build(tickers, frame)


def run(market, constraint_dict, **payload):
    """Optimize with ``constraint_dict``, defaulting to an all-cash portfolio."""
    body = {"initial_cash": 100_000, "universe": list(market.universe), **payload}
    body["constraints"] = constraint_dict
    return optimizer.optimize(body, market)


def rejection(market, constraint_dict, **payload):
    """The ValidationError raised by an infeasible request."""
    with pytest.raises(ValidationError) as excinfo:
        run(market, constraint_dict, **payload)
    return excinfo.value


def weights_of(result) -> dict:
    """Ticker -> percent weight of the returned portfolio."""
    return {ticker: row["percent_weight"] for ticker, row in result["optimized_portfolio"].items()}


def assert_honors(result, market, spec: dict) -> None:
    """Assert the *returned* portfolio satisfies every constraint in ``spec``.

    Extend this when adding a constraint: it is the single definition of
    "the plan obeys what was asked", used by every feasibility test below.
    """
    weights = weights_of(result)
    reserve = spec.get("cash_reserve", 0.0)
    assert weights, "a feasible request must return a portfolio"
    assert sum(weights.values()) == pytest.approx(
        (1 - reserve) * 100, abs=0.5
    ), "everything but the cash reserve must be invested"
    assert result["cash"]["percent_weight"] == pytest.approx(reserve * 100, abs=0.01)
    assert result["cash"]["value"] == pytest.approx(
        reserve * result["metrics"]["portfolio_value"], abs=0.02
    ), "the cash line and the weights must describe the same money"
    total = result["metrics"]["portfolio_value"]
    bought = sum(trade["value"] for trade in result["trades"] if trade["action"] == "buy")
    assert bought <= (1 - reserve) * total + 1e-4 * total, "buys must not dip into the reserve (share rounding aside)"
    if "max_weight" in spec:
        # Weight constraints are fractions of the whole portfolio, reserve
        # included -- "no stock above 10%" must not mean 10% of what is left.
        worst = max(weights.values())
        assert worst <= spec["max_weight"] * 100 + SLACK, f"max_weight {spec['max_weight']} exceeded by {worst}%"
    if "max_positions" in spec:
        assert len(weights) <= spec["max_positions"]
    if spec.get("min_universal_weight"):
        # The floor binds every universe ticker, so all of them must be held.
        held = set(weights)
        assert held == set(market.universe), "a weight floor applies to the whole universe"
        assert min(weights.values()) >= spec["min_universal_weight"] * 100 - SLACK
    if "max_turnover" in spec:
        assert result["metrics"]["turnover"] <= spec["max_turnover"] + 1e-3
    for ticker in result.get("unoptimized", {}).get("positions", {}):
        # A position kept as held is in the plan at its held size and is never traded.
        assert ticker in weights and not any(trade["ticker"] == ticker for trade in result["trades"])


# --- honored: the returned plan obeys what was asked ------------------------


@pytest.mark.parametrize(
    "spec",
    [
        # Regression: the cap truncated the solve and rescaled the survivors to
        # sum to one, so a 10% cap came back holding 14%. The cap is now
        # applied by re-solving over the names it keeps, which leaves max_weight binding.
        {"max_weight": 0.1, "max_positions": 12},
        {"max_weight": 0.1, "max_positions": 10},
        {"max_weight": 0.15, "max_positions": 8},
        {"max_weight": 0.2, "max_positions": 6},
        {"max_weight": 0.25, "max_positions": 5},
        {"max_weight": 0.34, "max_positions": 3},
        {"max_weight": 0.5, "max_positions": 2},
        {"max_positions": 4},
        {"max_weight": 0.2},
        {"min_universal_weight": 0.02},
        {"min_universal_weight": 0.03, "max_weight": 0.2},
        {"cash_reserve": 0.3},
        # With a 30% reserve and a 10% cap the invested 70% needs seven names.
        # If the cap were quietly measured against the invested sleeve
        # instead, five names at 14% of it would satisfy the solver.
        {"cash_reserve": 0.3, "max_weight": 0.1},
        {"cash_reserve": 0.2, "max_weight": 0.1, "max_positions": 8},
        {"cash_reserve": 0.2, "min_universal_weight": 0.02},
        {"cash_reserve": 0.5, "max_positions": 3},
    ],
)
def test_feasible_constraints_are_honored_by_the_returned_portfolio(wide, spec):
    """Each feasible combination comes back satisfying every constraint in it."""
    assert_honors(run(wide, spec), wide, spec)


def test_a_position_cap_no_longer_sells_past_max_turnover(wide):
    """Regression: positions the cap dropped were sold on top of the solver's budget.

    The limit was enforced against the full-universe solve, then the
    truncation sold more, and the response reported a turnover above the
    limit it had been given. Now the cap either respects the budget or the
    request is refused.
    """
    holdings = {ticker: 50.0 for ticker in list(wide.universe)[:10]}
    spec = {"max_turnover": 0.9, "max_positions": 5}
    result = run(wide, spec, initial_cash=1000.0, initial_holdings=holdings)
    assert_honors(result, wide, spec)


def test_a_position_cap_that_cannot_fit_the_turnover_budget_is_refused(wide):
    """The alternative to silently overselling: say so, and name both fields."""
    holdings = {ticker: 50.0 for ticker in list(wide.universe)[:10]}
    error = rejection(wide, {"max_turnover": 0.05, "max_positions": 5}, initial_cash=1000.0, initial_holdings=holdings)
    assert error.message.startswith("Infeasible.")
    assert "max_positions" in error.message and "max_turnover" in error.message
    fields = error.details["conflicts"][0]["fields"]
    assert "constraints.max_positions" in fields and "constraints.max_turnover" in fields


def test_capping_positions_can_only_improve_the_objective_it_optimizes(wide):
    """Re-solving over the kept names is never worse than rescaling them.

    The rescaled weights stay available to the restricted problem, so the
    re-solve is at least as good. This is the reason the fix is not a
    trade-off against solution quality.
    """
    full = run(wide, {})
    capped = run(wide, {"max_positions": 5})
    kept = sorted(weights_of(full), key=weights_of(full).get, reverse=True)[:5]
    rescaled = {t: weights_of(full)[t] / sum(weights_of(full)[k] for k in kept) for t in kept}
    vector = pd.Series(rescaled, dtype=float).reindex(wide.prices.columns, fill_value=0.0)
    mu, cov = wide.stats_for(list(wide.universe))
    mu = mu.reindex(wide.prices.columns).fillna(0.0)
    cov = cov.reindex(index=wide.prices.columns, columns=wide.prices.columns).fillna(0.0)
    rescaled_sharpe = float(vector @ mu) / float(np.sqrt(vector @ cov @ vector))
    assert capped["metrics"]["sharpe_ratio"] >= rescaled_sharpe - 1e-6


# --- the cash reserve: part of the plan, not a leftover ---------------------


def test_a_reserve_makes_the_once_impossible_example_feasible(wide):
    """8 holdings at 10% cannot fill a portfolio -- unless 20% stays in cash by design."""
    spec = {"max_weight": 0.1, "max_positions": 8, "cash_reserve": 0.2}
    assert_honors(run(wide, spec), wide, spec)
    # One notch less reserve and the same arithmetic fails again, saying how.
    error = rejection(wide, {"max_weight": 0.1, "max_positions": 8, "cash_reserve": 0.1})
    assert "cash_reserve" in error.message
    assert "raise cash_reserve to at least 0.2" in error.message


def test_a_reserve_the_floor_overfills_is_refused_naming_the_reserve(wide):
    """20 tickers at a 4% floor fit exactly in 80%; a 30% reserve leaves them 10% short."""
    spec = {"min_universal_weight": 0.04, "cash_reserve": 0.2}
    assert_honors(run(wide, spec), wide, spec)
    error = rejection(wide, {"min_universal_weight": 0.04, "cash_reserve": 0.3})
    fields = {field for conflict in error.details["conflicts"] for field in conflict["fields"]}
    assert "constraints.cash_reserve" in fields
    assert "lower cash_reserve to at most 0.2" in error.message


def test_raising_a_reserve_out_of_a_full_portfolio_needs_turnover_room(wide):
    """Fully invested, 20% reserve wanted: that 20% can only come from selling."""
    holdings = {ticker: 50.0 for ticker in list(wide.universe)[:10]}
    error = rejection(wide, {"cash_reserve": 0.2, "max_turnover": 0.1}, initial_cash=0.0, initial_holdings=holdings)
    assert error.message.startswith("Infeasible.")
    assert "cash_reserve" in error.message and "selling" in error.message
    # With room to sell, the reserve is raised and the limit still holds.
    spec = {"cash_reserve": 0.2, "max_turnover": 0.35}
    result = run(wide, spec, initial_cash=0.0, initial_holdings=holdings)
    assert_honors(result, wide, spec)
    assert result["metrics"]["turnover"] >= 0.2 - 1e-3, "raising the reserve is itself selling"


def test_the_sharpe_ratio_does_not_depend_on_the_reserve(wide):
    """Scaling between cash and the tangency portfolio moves along the capital
    allocation line: return and volatility shrink together, so the ratio holds."""
    invested = run(wide, {}, risk_free_rate=0.03)
    reserved = run(wide, {"cash_reserve": 0.4}, risk_free_rate=0.03)
    assert reserved["metrics"]["sharpe_ratio"] == pytest.approx(invested["metrics"]["sharpe_ratio"], abs=0.02)
    expected = 0.6 * invested["metrics"]["expected_annual_return"] + 0.4 * 0.03
    assert reserved["metrics"]["expected_annual_return"] == pytest.approx(expected, abs=0.002)
    assert reserved["metrics"]["annual_volatility"] == pytest.approx(
        0.6 * invested["metrics"]["annual_volatility"], abs=0.002
    )


# --- refused: contradictions are caught before the solver -------------------


@pytest.mark.parametrize(
    "spec, expected_fields",
    [
        (
            {"max_weight": 0.1, "max_positions": 8},
            {"constraints.max_positions", "constraints.max_weight"},
        ),
        (
            {"max_weight": 0.02},
            {"constraints.max_weight", "universe"},
        ),
        (
            {"min_universal_weight": 0.1},
            {"constraints.min_universal_weight", "universe"},
        ),
        (
            {"min_universal_weight": 0.02, "max_positions": 5},
            {"constraints.min_universal_weight", "constraints.max_positions"},
        ),
        (
            {"min_universal_weight": 0.5, "max_weight": 0.3},
            {"constraints.min_universal_weight", "constraints.max_weight"},
        ),
    ],
)
def test_contradictions_are_refused_naming_their_fields_and_a_remedy(wide, spec, expected_fields):
    """Each contradiction is rejected before the solver, naming its fields and a way out."""
    error = rejection(wide, spec)
    assert error.message.startswith("Infeasible.")
    reported = {field for conflict in error.details["conflicts"] for field in conflict["fields"]}
    assert expected_fields <= reported
    for conflict in error.details["conflicts"]:
        assert conflict["reason"] and conflict["remedy"], "every conflict must say what is wrong and what to change"


def test_the_users_example_is_refused_with_both_ways_out(wide):
    """10% per holding across 8 holdings only ever invests 80% of the money."""
    message = rejection(wide, {"max_weight": 0.1, "max_positions": 8}).message
    assert "80%" in message
    assert "max_positions to at least 10" in message
    assert "max_weight to at least 0.13" in message


@pytest.mark.parametrize(
    "spec",
    [
        {"max_weight": 0.1, "max_positions": 10},  # exactly 100%: feasible
        {"max_weight": 0.13, "max_positions": 8},  # the remedy the message suggests
        {"min_universal_weight": 0.05},  # 20 x 5% is exactly 100%
    ],
)
def test_the_boundary_case_is_feasible_not_refused(wide, spec):
    """A constraint set that exactly saturates the portfolio must solve.

    Guards the rules against an off-by-one that would reject the very values
    the remedies recommend.
    """
    assert_honors(run(wide, spec), wide, spec)


@pytest.mark.parametrize(
    "spec",
    [
        {"max_weight": 0.09, "max_positions": 10},
        {"max_weight": 0.12, "max_positions": 8},
        {"min_universal_weight": 0.06},
    ],
)
def test_one_step_past_the_boundary_is_refused(wide, spec):
    """One notch tighter than a saturating set cannot fill the portfolio."""
    assert rejection(wide, spec).message.startswith("Infeasible.")


def test_every_conflict_is_reported_not_just_the_first(wide):
    """Three contradictions should cost one round trip, not three."""
    error = rejection(wide, {"min_universal_weight": 0.5, "max_weight": 0.3, "max_positions": 2})
    assert len(error.details["conflicts"]) > 1
    assert "constraint conflicts" in error.message
    reported = {field for conflict in error.details["conflicts"] for field in conflict["fields"]}
    assert {"constraints.min_universal_weight", "constraints.max_weight", "constraints.max_positions"} <= reported


def test_a_small_universe_limits_weights_just_as_a_position_cap_would(wide):
    """max_weight is checked against the universe even when max_positions is unset.

    Four tickers at 20% each is 80% of the portfolio whether or not anyone
    wrote down a position cap, and the solver's bare "infeasible" was the
    only previous answer.
    """
    body = {"initial_cash": 10_000, "universe": list(wide.universe)[:4], "constraints": {"max_weight": 0.2}}
    with pytest.raises(ValidationError) as excinfo:
        optimizer.optimize(body, wide)
    assert "universe" in str(excinfo.value.details["conflicts"][0]["fields"])


# --- the same refusal, through every way in --------------------------------


CONFLICTING = {"max_weight": 0.1, "max_positions": 2}


def test_api_refuses_with_a_machine_readable_payload_whose_remedy_works(client):
    """An agent must be able to branch on the fields, not parse the prose; and the suggested fix must work."""
    response = client.post("/api/optimize/", json={"initial_cash": 10_000, "constraints": CONFLICTING})
    assert response.status_code == 400
    error = response.get_json()["error"]
    assert error["message"].startswith("Infeasible.") and "max_weight to at least 0.5" in error["message"]
    conflicts = error["payload"]["conflicts"]
    assert conflicts and all({"fields", "reason", "remedy"} == set(c) for c in conflicts)
    assert any("constraints.max_weight" in c["fields"] for c in conflicts)
    accepted = client.post(
        "/api/optimize/", json={"initial_cash": 10_000, "constraints": {"max_weight": 0.5, "max_positions": 2}}
    )
    assert accepted.status_code == 200
    assert max(row["percent_weight"] for row in accepted.get_json()["optimized_portfolio"].values()) <= 50 + SLACK


def test_browser_refuses_on_the_page_with_only_the_submitted_form_still_filled_in(workspace_client, repo):
    """A rejected run is one edit away from a good one, so nothing typed is thrown away.

    Only the form that was submitted comes back filled; other portfolios'
    forms stay blank, and a plain visit afterwards is clean again.
    """
    workspace_client.post("/portfolios/create/", data={"name": "second", "cash": "5000"})
    pid = repo.get_portfolio("tester", name="main").id
    response = workspace_client.post(
        "/optimizer/run/",
        data={"portfolio_id": pid, "universe": "top20", "max_weight": "0.1", "max_positions": "2"},
    )
    assert response.status_code == 200
    page = response.data.decode()
    assert "Infeasible." in page and "max_weight to at least" in page
    assert page.count('value="0.1"') == 1 and 'value="2"' in page, "the other portfolio's form is not prefilled"
    assert 'value="top20"' in page and "selected" in page
    assert 'value="0.1"' not in workspace_client.get("/workspace/").data.decode()


def test_browser_cash_reserve_reaches_the_optimizer_and_the_results_page(workspace_client, repo):
    """The form's reserve becomes the constraint, and the results table shows the cash line."""
    pid = repo.get_portfolio("tester", name="main").id
    response = workspace_client.post(
        "/optimizer/run/", data={"portfolio_id": pid, "universe": "top20", "cash_reserve": "0.25"}
    )
    assert response.status_code == 200
    page = response.data.decode()
    assert "Cash reserve" in page and "25.0%" in page
    # A rejected reserve comes back editable like every other constraint.
    rejected = workspace_client.post(
        "/optimizer/run/", data={"portfolio_id": pid, "universe": "top20", "cash_reserve": "1.5"}
    ).data.decode()
    assert "cash_reserve" in rejected and 'value="1.5"' in rejected


def test_every_rule_returns_a_usable_conflict_or_none(wide):
    """A structural check on the registry, so a new rule cannot return prose by mistake."""
    request = optimizer.OptimizationRequest(
        universe=list(wide.universe)[:4],
        max_weight=0.1,
        min_universal_weight=0.5,
        max_positions=1,
        unoptimized={"T19": 0.3},
        unoptimized_days={"T19": 20},
    )
    for rule in constraints.RULES:
        found = rule(request)
        assert found is None or isinstance(found, constraints.Conflict)
        if found is not None:
            assert isinstance(found.fields, tuple) and found.fields
            assert found.reason.strip() and found.remedy.strip()
            assert set(found.as_dict()) == {"fields", "reason", "remedy"}
    fine = optimizer.parse_request(
        {"initial_cash": 10_000, "universe": list(wide.universe), "constraints": {"max_weight": 0.2}}, wide
    )
    assert constraints.conflicts(fine) == [], "the registry stays silent on a request that is fine"
