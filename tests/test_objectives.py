"""Optimization objectives beyond max-Sharpe: validation, the math, cash answers, unreachable targets, transports.

Fixture: five stocks whose mean historical returns range from AMZN at about
-10% to NVDA at about 69%; the calmest fully-invested portfolio has about
8.7% volatility.
"""

import dataclasses
import datetime
import json
import math

import numpy as np
import pandas as pd
import pytest

from tests.conftest import DEV_USER_TOKEN, FIXTURES_DIR
from tests.test_charge_for_answers import uses
from wolvwealth.backtest import BacktestConfig, Frequency, OptimizerStrategy, run_backtest
from wolvwealth.backtest.cli import main
from wolvwealth.backtest.request import BacktestLimits, parse_backtest_request
from wolvwealth.optimizer_core import (
    CashBeatsRiskBudget,
    CashBeatsUtility,
    CashMeetsReturnTarget,
    HoldCashAnswer,
    NoAssetBeatsRiskFreeRate,
    TargetUnreachable,
    compute_max_sharpe_portfolio,
    compute_portfolio,
    estimate_inputs,
    highest_reachable_return,
    portfolio_performance,
)
from wolvwealth.optimizer_params import InvalidOptimizationInput, Objective, ObjectiveName, parse_objective

HEADERS = {"Authorization": DEV_USER_TOKEN}
TOLERANCE = 1e-4


def fixture_prices() -> pd.DataFrame:
    return pd.read_csv(FIXTURES_DIR / "historical_prices.csv", parse_dates=True, index_col="Date")


def lowest_volatility(**constraints) -> float:
    return compute_portfolio(fixture_prices(), Objective(ObjectiveName.MIN_VOLATILITY), **constraints)[1][1]


EVERY_OBJECTIVE = [
    Objective(ObjectiveName.MAX_SHARPE),
    Objective(ObjectiveName.MIN_VOLATILITY),
    Objective(ObjectiveName.EFFICIENT_RISK, target_volatility=0.12),
    Objective(ObjectiveName.EFFICIENT_RETURN, target_return=0.3),
    Objective(ObjectiveName.MAX_UTILITY, risk_aversion=10),
]


class TestObjectiveValidation:
    def test_default_is_max_sharpe(self):
        assert Objective().name == ObjectiveName.MAX_SHARPE
        assert Objective().to_dict() == {"name": "max_sharpe"}

    def test_bare_name_and_object_forms(self):
        assert parse_objective("min_volatility") == Objective(ObjectiveName.MIN_VOLATILITY)
        assert parse_objective({"name": "MIN_VOLATILITY"}) == Objective(ObjectiveName.MIN_VOLATILITY)
        parsed = parse_objective({"name": "efficient_risk", "target_volatility": 0.15})
        assert parsed.to_dict() == {"name": "efficient_risk", "target_volatility": 0.15}

    def test_plain_string_name_and_int_parameter_are_normalized(self):
        """Library and CLI callers pass strings and ints; pypfopt's efficient_return rejects an int target."""
        objective = Objective("efficient_return", target_return=1)
        assert objective.name is ObjectiveName.EFFICIENT_RETURN
        assert isinstance(objective.target_return, float)
        assert hash(objective) == hash(Objective(ObjectiveName.EFFICIENT_RETURN, target_return=1.0))

    @pytest.mark.parametrize(
        ("raw", "message"),
        [
            ("max_return", "must be one of: max_sharpe, min_volatility"),
            ({"target_volatility": 0.1}, "must be one of"),
            (42, "must be a name or an object"),
            (["max_sharpe"], "must be a name or an object"),
            ({"name": "max_sharpe", "risk_tolerance": 2}, "Unknown objective field(s): risk_tolerance"),
            ({"name": "efficient_risk"}, "efficient_risk requires target_volatility"),
            ("max_utility", "max_utility requires risk_aversion"),
            ({"name": "max_sharpe", "target_return": 0.1}, "max_sharpe does not take target_return"),
            ({"name": "efficient_risk", "target_volatility": 0.1, "risk_aversion": 2}, "does not take risk_aversion"),
            ({"name": "efficient_risk", "target_volatility": 0}, "greater than 0"),
            ({"name": "efficient_risk", "target_volatility": 6}, "at most 5"),
            ({"name": "efficient_risk", "target_volatility": True}, "must be a number"),
            ({"name": "efficient_risk", "target_volatility": "0.1"}, "must be a number"),
            ({"name": "efficient_return", "target_return": math.nan}, "finite"),
            ({"name": "efficient_return", "target_return": -1.5}, "at least -1"),
            ({"name": "max_utility", "risk_aversion": -1}, "greater than 0"),
            ({"name": "max_utility", "risk_aversion": 10**400}, "out of range"),
        ],
    )
    def test_rejects(self, raw, message):
        with pytest.raises(InvalidOptimizationInput, match=message.replace("(", r"\(").replace(")", r"\)")):
            parse_objective(raw)


class TestEveryObjective:
    @pytest.mark.parametrize("objective", EVERY_OBJECTIVE, ids=lambda o: str(o.name))
    def test_fully_invested_and_constraints_hold(self, objective):
        weights, performance = compute_portfolio(fixture_prices(), objective, max_weight=0.4, min_universal_weight=0.05)
        assert sum(weights.values()) == pytest.approx(1.0, abs=1e-9)
        assert set(weights) == set(fixture_prices().columns)  # min_universal_weight keeps every asset
        assert all(0.05 - TOLERANCE <= weight <= 0.4 + TOLERANCE for weight in weights.values())
        mu, cov = estimate_inputs(fixture_prices())
        assert performance == pytest.approx(portfolio_performance(weights, mu, cov))

    @pytest.mark.parametrize("objective", EVERY_OBJECTIVE, ids=lambda o: str(o.name))
    def test_max_positions(self, objective):
        weights, _ = compute_portfolio(fixture_prices(), objective, max_positions=3, max_weight=0.5)
        assert len(weights) <= 3
        assert max(weights.values()) <= 0.5 + TOLERANCE

    def test_old_entry_point_is_max_sharpe(self):
        assert compute_max_sharpe_portfolio(fixture_prices(), max_positions=3) == compute_portfolio(
            fixture_prices(), max_positions=3
        )


class TestMinVolatility:
    def test_calmer_than_max_sharpe_and_any_single_stock(self):
        _, (_, min_vol, _) = compute_portfolio(fixture_prices(), Objective(ObjectiveName.MIN_VOLATILITY))
        _, (_, sharpe_vol, _) = compute_portfolio(fixture_prices())
        _, cov = estimate_inputs(fixture_prices())
        assert min_vol < sharpe_vol
        assert min_vol < np.sqrt(np.diag(cov)).min()

    def test_never_holds_cash(self):
        """Cash is trivially the calmest holding; this objective is about the calmest invested portfolio."""
        weights, _ = compute_portfolio(fixture_prices(), Objective(ObjectiveName.MIN_VOLATILITY), risk_free_rate=10.0)
        assert sum(weights.values()) == pytest.approx(1.0)


class TestEfficientRisk:
    @pytest.mark.parametrize("target", [0.09, 0.12, 0.15])
    def test_stays_within_the_limit_and_beats_the_calmest_portfolio_on_return(self, target):
        _, (ret, vol, _) = compute_portfolio(
            fixture_prices(), Objective(ObjectiveName.EFFICIENT_RISK, target_volatility=target)
        )
        _, (calm_ret, _, _) = compute_portfolio(fixture_prices(), Objective(ObjectiveName.MIN_VOLATILITY))
        assert vol <= target + TOLERANCE
        assert ret > calm_ret

    def test_more_risk_budget_never_lowers_expected_return(self):
        returns = [
            compute_portfolio(fixture_prices(), Objective(ObjectiveName.EFFICIENT_RISK, target_volatility=v))[1][0]
            for v in (0.09, 0.11, 0.13, 0.15)
        ]
        assert returns == sorted(returns)

    def test_a_loose_limit_gives_the_highest_return_portfolio(self):
        weights, _ = compute_portfolio(fixture_prices(), Objective(ObjectiveName.EFFICIENT_RISK, target_volatility=1.0))
        assert weights == {"NVDA": pytest.approx(1.0)}

    def test_a_limit_below_the_calmest_portfolio_says_what_would_work(self):
        lowest = lowest_volatility()
        with pytest.raises(TargetUnreachable) as excinfo:
            compute_portfolio(fixture_prices(), Objective(ObjectiveName.EFFICIENT_RISK, target_volatility=0.05))
        message = str(excinfo.value)
        assert f"{lowest:.2%}" in message
        hint = math.ceil(lowest * 1000) / 1000
        assert f"at least {hint:.3f}" in message
        assert "min_volatility" in message
        weights, (_, vol, _) = compute_portfolio(
            fixture_prices(), Objective(ObjectiveName.EFFICIENT_RISK, target_volatility=hint)
        )
        assert vol <= hint + TOLERANCE  # The hint works.

    def test_a_limit_at_the_calmest_portfolio_returns_it(self):
        """Exactly on the boundary the solver tends to fail numerically; the answer is known without it."""
        lowest = lowest_volatility()
        calm, _ = compute_portfolio(fixture_prices(), Objective(ObjectiveName.MIN_VOLATILITY))
        weights, _ = compute_portfolio(
            fixture_prices(), Objective(ObjectiveName.EFFICIENT_RISK, target_volatility=lowest)
        )
        assert weights == pytest.approx(calm)

    def test_is_checked_against_the_real_constraints(self):
        """pypfopt compares with the unconstrained (short-selling) minimum, which no long-only portfolio reaches."""
        lowest = lowest_volatility(max_weight=0.21)  # Binds: the unconstrained calmest portfolio has MSFT at ~24%.
        assert lowest > lowest_volatility() + 1e-4
        with pytest.raises(TargetUnreachable, match="Unreachable target_volatility"):
            compute_portfolio(
                fixture_prices(),
                Objective(ObjectiveName.EFFICIENT_RISK, target_volatility=(lowest + lowest_volatility()) / 2),
                max_weight=0.21,
            )

    def test_holds_cash_when_no_stock_beats_the_rate(self):
        with pytest.raises(NoAssetBeatsRiskFreeRate, match="target-volatility recommendation is to hold cash"):
            compute_portfolio(
                fixture_prices(), Objective(ObjectiveName.EFFICIENT_RISK, target_volatility=0.2), risk_free_rate=10.0
            )

    def test_holds_cash_when_nothing_within_the_limit_beats_the_rate(self):
        """NVDA beats the rate, but nothing calm enough for a 9% limit does, and cash earns it at 0% volatility."""
        objective = Objective(ObjectiveName.EFFICIENT_RISK, target_volatility=0.09)
        best_within_limit = compute_portfolio(fixture_prices(), objective)[1][0]
        rate = best_within_limit + 0.01
        assert rate < estimate_inputs(fixture_prices())[0].max()  # So the "no asset beats it" shortcut cannot fire.
        with pytest.raises(CashBeatsRiskBudget) as excinfo:
            compute_portfolio(fixture_prices(), objective, risk_free_rate=rate)
        assert excinfo.value.performance == (rate, 0.0, 0.0)
        assert "Within the 9.0% volatility limit" in str(excinfo.value)


class TestEfficientReturn:
    @pytest.mark.parametrize("target", [0.25, 0.4, 0.6])
    def test_reaches_the_target_more_calmly_than_max_return(self, target):
        _, (ret, vol, _) = compute_portfolio(
            fixture_prices(), Objective(ObjectiveName.EFFICIENT_RETURN, target_return=target)
        )
        assert ret >= target - TOLERANCE
        assert vol < estimate_inputs(fixture_prices())[1].loc["NVDA", "NVDA"] ** 0.5

    def test_a_target_the_calmest_portfolio_beats_gives_the_calmest_portfolio(self):
        calm, _ = compute_portfolio(fixture_prices(), Objective(ObjectiveName.MIN_VOLATILITY))
        weights, _ = compute_portfolio(fixture_prices(), Objective(ObjectiveName.EFFICIENT_RETURN, target_return=0.05))
        assert weights == pytest.approx(calm, abs=1e-3)

    @pytest.mark.parametrize("target", [0.02, 0.01, -0.5])
    def test_a_target_cash_meets_is_a_hold_cash_answer(self, target):
        with pytest.raises(CashMeetsReturnTarget, match="already meets the .* return target") as excinfo:
            compute_portfolio(fixture_prices(), Objective(ObjectiveName.EFFICIENT_RETURN, target_return=target))
        assert excinfo.value.performance == (0.02, 0.0, 0.0)

    def test_an_unreachable_target_says_what_would_work(self):
        mu, _ = estimate_inputs(fixture_prices())
        with pytest.raises(TargetUnreachable) as excinfo:
            compute_portfolio(fixture_prices(), Objective(ObjectiveName.EFFICIENT_RETURN, target_return=0.8))
        assert f"{mu.max():.2%}" in str(excinfo.value)
        hint = math.floor(mu.max() * 1000) / 1000
        assert f"at most {hint:.3f}" in str(excinfo.value)
        weights, (ret, _, _) = compute_portfolio(
            fixture_prices(), Objective(ObjectiveName.EFFICIENT_RETURN, target_return=hint)
        )
        assert ret >= hint - TOLERANCE  # The hint works.

    @pytest.mark.parametrize("bounds", [(0.0, 1.0), (0.0, 0.4), (0.05, 0.3)])
    def test_the_highest_reachable_return_is_reachable(self, bounds):
        """Regression: pypfopt's solved maximum is a hair below the exact one, so it refused this target."""
        min_weight, max_weight = bounds
        mu, _ = estimate_inputs(fixture_prices())
        highest = highest_reachable_return(mu, min_weight, max_weight)
        _, (ret, _, _) = compute_portfolio(
            fixture_prices(),
            Objective(ObjectiveName.EFFICIENT_RETURN, target_return=highest),
            min_universal_weight=min_weight,
            max_weight=max_weight,
        )
        assert ret == pytest.approx(highest, abs=TOLERANCE)

    def test_reachability_respects_max_weight(self):
        """With NVDA capped at 40%, 69% is out of reach although NVDA alone returns it."""
        with pytest.raises(TargetUnreachable, match="Unreachable target_return"):
            compute_portfolio(
                fixture_prices(), Objective(ObjectiveName.EFFICIENT_RETURN, target_return=0.6), max_weight=0.4
            )

    def test_max_positions_shortfall_is_explained(self):
        """max_positions keeps the largest positions of the first solve, which may not reach the target."""
        with pytest.raises(TargetUnreachable) as excinfo:
            compute_portfolio(
                fixture_prices(), Objective(ObjectiveName.EFFICIENT_RETURN, target_return=0.15), max_positions=2
            )
        message = str(excinfo.value)
        assert "2 largest positions max_positions kept" in message
        assert "raise max_positions" in message


class TestHighestReachableReturn:
    def test_greedy_fill_is_exact(self):
        mu = pd.Series({"A": 0.3, "B": 0.2, "C": 0.1})
        assert highest_reachable_return(mu, 0.0, 1.0) == pytest.approx(0.3)
        assert highest_reachable_return(mu, 0.0, 0.5) == pytest.approx(0.5 * 0.3 + 0.5 * 0.2)
        assert highest_reachable_return(mu, 0.1, 0.6) == pytest.approx(0.6 * 0.3 + 0.3 * 0.2 + 0.1 * 0.1)

    def test_integer_bounds(self):
        """Regression: an int min_weight made an int array, truncating every fill to 0 (and the maximum to 0%)."""
        mu = pd.Series({"A": 0.3, "B": 0.2, "C": 0.1})
        assert highest_reachable_return(mu, 0, 0.5) == pytest.approx(0.25)

    def test_unknown_returns_leave_the_check_to_the_solver(self):
        assert highest_reachable_return(pd.Series({"A": 0.1, "B": np.nan}), 0.0, 1.0) == math.inf


class TestMaxUtility:
    def test_more_risk_aversion_never_raises_volatility(self):
        vols = [
            compute_portfolio(fixture_prices(), Objective(ObjectiveName.MAX_UTILITY, risk_aversion=a))[1][1]
            for a in (1, 5, 20, 50)
        ]
        assert vols == sorted(vols, reverse=True)

    def test_extreme_caution_holds_cash(self):
        """At risk aversion 1000 even the calmest portfolio's penalty dwarfs its return; cash has no penalty."""
        with pytest.raises(CashBeatsUtility):
            compute_portfolio(fixture_prices(), Objective(ObjectiveName.MAX_UTILITY, risk_aversion=1000))

    def test_low_risk_aversion_chases_return(self):
        weights, _ = compute_portfolio(fixture_prices(), Objective(ObjectiveName.MAX_UTILITY, risk_aversion=0.5))
        assert weights == {"NVDA": pytest.approx(1.0)}

    def test_holds_cash_when_the_risk_penalty_eats_the_premium(self):
        """At a 30% rate NVDA alone beats cash, but not once a cautious investor's risk penalty is taken off."""
        with pytest.raises(CashBeatsUtility) as excinfo:
            compute_portfolio(
                fixture_prices(), Objective(ObjectiveName.MAX_UTILITY, risk_aversion=100), risk_free_rate=0.30
            )
        answer = excinfo.value
        assert answer.best_return - answer.penalty <= 0.30
        assert "At risk aversion 100" in str(answer)
        assert "max-utility recommendation is to hold cash" in str(answer)

    def test_stays_invested_when_the_premium_survives_the_penalty(self):
        weights, _ = compute_portfolio(
            fixture_prices(), Objective(ObjectiveName.MAX_UTILITY, risk_aversion=1), risk_free_rate=0.30
        )
        assert weights


class TestAnswerTypes:
    def test_cash_answers_are_not_errors_and_unreachable_targets_are_input_errors(self):
        for answer in (NoAssetBeatsRiskFreeRate, CashMeetsReturnTarget, CashBeatsRiskBudget, CashBeatsUtility):
            assert issubclass(answer, HoldCashAnswer)
            assert not issubclass(answer, ValueError)
        assert issubclass(TargetUnreachable, InvalidOptimizationInput)

    def test_max_sharpe_explanation_is_unchanged(self):
        assert "so the max-Sharpe recommendation is to hold cash" in str(NoAssetBeatsRiskFreeRate(0.02, "A", -0.1))


class TestOptimizeApi:
    def post(self, client, **body):
        return client.post("/api/optimize/", json={"initial_cash": 1000, "universe": ["top5"], **body}, headers=HEADERS)

    def test_default_is_max_sharpe_and_is_echoed(self, client):
        data = self.post(client).get_json()
        assert data["objective"] == {"name": "max_sharpe"}

    def test_min_volatility_by_name(self, client):
        data = self.post(client, objective="min_volatility").get_json()
        assert data["objective"] == {"name": "min_volatility"}
        assert data["metrics"]["annual_volatility"] == pytest.approx(lowest_volatility(), abs=1e-3)

    def test_efficient_risk_object_form(self, client):
        response = self.post(client, objective={"name": "efficient_risk", "target_volatility": 0.12})
        assert response.status_code == 200, response.get_json()
        data = response.get_json()
        assert data["objective"] == {"name": "efficient_risk", "target_volatility": 0.12}
        assert data["metrics"]["annual_volatility"] <= 0.12 + 1e-3

    def test_constraints_apply_with_any_objective(self, client):
        response = self.post(
            client, objective={"name": "max_utility", "risk_aversion": 1}, constraints={"max_weight": 0.3}
        )
        weights = [holding["percent_weight"] for holding in response.get_json()["optimized_portfolio"].values()]
        assert max(weights) <= 30.0 + 0.01

    @pytest.mark.parametrize(
        ("objective", "message"),
        [
            ("max_return", "Invalid objective"),
            ({"name": "efficient_return"}, "requires target_return"),
            ({"name": "efficient_risk", "target_volatility": 0.01}, "Unreachable target_volatility"),
            ({"name": "efficient_return", "target_return": 0.9}, "Unreachable target_return"),
        ],
    )
    def test_bad_or_unreachable_objectives_are_free_400s(self, app, client, objective, message):
        before = uses(app)
        response = self.post(client, objective=objective)
        assert response.status_code == 400
        assert message in response.get_json()["error"]["message"]
        assert uses(app) == before

    def test_a_return_target_cash_meets_is_a_charged_hold_cash_answer(self, app, client):
        before = uses(app)
        response = self.post(client, objective={"name": "efficient_return", "target_return": 0.01})
        assert response.status_code == 200
        data = response.get_json()
        assert data["optimized_portfolio"] == {}
        assert data["cash"]["percent_weight"] == 100.0
        assert "already meets the 1.0% return target" in data["notes"][0]
        assert uses(app) == before - 1


def backtest_config(**overrides) -> BacktestConfig:
    prices = fixture_prices()
    values = {
        "start": datetime.date(2023, 7, 1),
        "end": prices.index[-1].date(),
        "universe": tuple(prices.columns),
        "frequency": Frequency.QUARTERLY,
        "lookback_days": 120,
    }
    return BacktestConfig(**{**values, **overrides})


class TestBacktest:
    def test_strategy_runs_the_configured_objective(self):
        cfg = backtest_config(objective=Objective(ObjectiveName.MIN_VOLATILITY))
        strategy = OptimizerStrategy(cfg.constraints, objective=cfg.objective)
        result = run_backtest(fixture_prices(), cfg, strategy)
        assert result.strategy == "min_volatility"
        assert result.rebalances
        for event in result.rebalances:
            assert sum(event.weights.values()) == pytest.approx(1.0, abs=1e-6)

    def test_default_strategy_is_still_called_max_sharpe(self):
        assert OptimizerStrategy().name == "max_sharpe"

    def test_an_unreachable_target_in_a_window_holds_and_warns(self):
        cfg = backtest_config(objective=Objective(ObjectiveName.EFFICIENT_RISK, target_volatility=0.01))
        result = run_backtest(fixture_prices(), cfg, OptimizerStrategy(objective=cfg.objective))
        assert not result.rebalances
        assert any("Unreachable target_volatility" in warning for warning in result.warnings)

    def test_objective_is_part_of_the_config_and_its_cache_key(self):
        sharpe = backtest_config()
        utility = backtest_config(objective=Objective(ObjectiveName.MAX_UTILITY, risk_aversion=3))
        assert sharpe != utility
        assert hash(sharpe) != hash(utility)
        assert utility.to_dict()["objective"] == {"name": "max_utility", "risk_aversion": 3.0}
        assert sharpe.to_dict()["objective"] == {"name": "max_sharpe"}
        assert dataclasses.replace(utility, objective=Objective()) == sharpe

    def test_config_rejects_a_non_objective(self):
        with pytest.raises(InvalidOptimizationInput, match="objective must be an Objective"):
            backtest_config(objective="min_volatility")

    def test_request_parses_the_objective(self):
        universe = list(fixture_prices().columns)
        parse = lambda payload: parse_backtest_request(  # noqa: E731
            payload, ticker_universe=universe, prices=fixture_prices(), limits=BacktestLimits()
        )
        base = {"start": "2023-07-01", "universe": ["AAPL", "MSFT"]}
        assert parse(base).config.objective == Objective()
        parsed = parse({**base, "objective": {"name": "efficient_return", "target_return": 0.1}})
        assert parsed.config.objective == Objective(ObjectiveName.EFFICIENT_RETURN, target_return=0.1)
        with pytest.raises(InvalidOptimizationInput, match="requires risk_aversion"):
            parse({**base, "objective": "max_utility"})

    def test_cli_objective_flags(self, capsys):
        code = main(
            [
                "--start", "2023-07-01", "--universe", "top5", "--lookback-days", "120", "--benchmark",
                "--objective", "efficient_risk", "--target-volatility", "0.15", "--json", "--no-equity",
            ]
        )  # fmt: skip
        assert code == 0
        data = json.loads(capsys.readouterr().out)
        assert data["config"]["objective"] == {"name": "efficient_risk", "target_volatility": 0.15}
        assert data["result"]["strategy"] == "efficient_risk"

    def test_cli_text_report_names_the_objective(self, capsys):
        code = main(
            ["--start", "2023-07-01", "--universe", "top5", "--lookback-days", "120", "--benchmark",
             "--objective", "max_utility", "--risk-aversion", "5"]
        )  # fmt: skip
        assert code == 0
        assert "objective max_utility (risk_aversion 5)" in capsys.readouterr().out

    def test_cli_rejects_a_missing_parameter(self, capsys):
        assert main(["--start", "2023-07-01", "--objective", "efficient_return"]) == 2
        assert "efficient_return requires target_return" in capsys.readouterr().err
