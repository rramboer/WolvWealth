"""Rebalancing from current holdings: turnover limits, trading costs, trade lists, and their cash and error cases.

Fixture: five stocks whose mean historical returns range from AMZN at about
-10% to NVDA at about 69%. "Losers" below is half AAPL, half AMZN, a
fully-invested portfolio expected to return about 0.8%, below the 2%
risk-free rate.
"""

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
    CashBeatsUtility,
    CashBlockedByTurnover,
    NoAssetBeatsRiskFreeRate,
    NoPortfolioBeatsRiskFreeRate,
    TargetUnreachable,
    TurnoverInfeasible,
    _Rebalance,
    build_portfolio_output,
    compute_portfolio,
    estimate_inputs,
    highest_net_return,
    plan_trades,
)
from wolvwealth.optimizer_params import (
    Constraints,
    InvalidOptimizationInput,
    Objective,
    ObjectiveName,
    parse_constraints,
)

HEADERS = {"Authorization": DEV_USER_TOKEN}
LOSERS = {"AAPL": 0.5, "AMZN": 0.5}
TOLERANCE = 1e-4

EVERY_OBJECTIVE = [
    Objective(ObjectiveName.MAX_SHARPE),
    Objective(ObjectiveName.MIN_VOLATILITY),
    Objective(ObjectiveName.EFFICIENT_RISK, target_volatility=0.15),
    Objective(ObjectiveName.EFFICIENT_RETURN, target_return=0.12),
    Objective(ObjectiveName.MAX_UTILITY, risk_aversion=10),
]
ids = lambda objective: str(objective.name)  # noqa: E731


def fixture_prices() -> pd.DataFrame:
    return pd.read_csv(FIXTURES_DIR / "historical_prices.csv", parse_dates=True, index_col="Date")


def rebalance(current: dict, cost_bps: float = 0.0, max_turnover: float | None = None) -> _Rebalance:
    return _Rebalance(pd.Series(current, dtype=float), cost_bps / 10_000, max_turnover)


def sold(current: dict, weights: dict) -> float:
    return rebalance(current).sold(weights)


def traded(current: dict, weights: dict) -> float:
    return rebalance(current).traded(weights)


class TestTurnoverArithmetic:
    def test_sold_is_one_way_and_investing_cash_is_free(self):
        assert sold({}, {"A": 1.0}) == 0.0
        assert traded({}, {"A": 1.0}) == 1.0
        assert sold({"A": 1.0}, {"B": 1.0}) == 1.0
        assert traded({"A": 1.0}, {"B": 1.0}) == 2.0
        assert sold({"A": 0.4}, {"A": 0.5, "B": 0.5}) == 0.0  # 60% cash invested, nothing sold
        assert sold({"A": 0.6, "B": 0.4}, {"A": 0.5, "B": 0.5}) == pytest.approx(0.1)

    def test_rebalancing_only_when_it_can_change_the_answer(self):
        assert not rebalance({}, cost_bps=10, max_turnover=0.1).active  # all cash: nothing to trade off
        assert not rebalance(LOSERS).active  # holdings, but neither a limit nor a cost
        assert rebalance(LOSERS, max_turnover=0.5).active
        assert rebalance(LOSERS, cost_bps=5).active


class TestStartingFromScratchIsUnchanged:
    @pytest.mark.parametrize("objective", EVERY_OBJECTIVE, ids=ids)
    def test_holdings_without_a_limit_or_cost(self, objective):
        assert compute_portfolio(fixture_prices(), objective, current_weights=LOSERS) == compute_portfolio(
            fixture_prices(), objective
        )

    @pytest.mark.parametrize("objective", EVERY_OBJECTIVE, ids=ids)
    def test_a_limit_or_cost_with_only_cash(self, objective):
        fresh = compute_portfolio(fixture_prices(), objective)
        limited = compute_portfolio(
            fixture_prices(), objective, current_weights={}, max_turnover=0.0, transaction_cost_bps=50
        )
        assert limited == fresh

    @pytest.mark.parametrize("objective", EVERY_OBJECTIVE, ids=ids)
    def test_a_limit_that_does_not_bind_gives_the_same_portfolio(self, objective):
        """Checks the rebalancing formulations, notably max-Sharpe's own, against pypfopt's."""
        fresh, _ = compute_portfolio(fixture_prices(), objective)
        limited, _ = compute_portfolio(fixture_prices(), objective, current_weights=LOSERS, max_turnover=1.0)
        for asset in set(fresh) | set(limited):
            assert limited.get(asset, 0.0) == pytest.approx(fresh.get(asset, 0.0), abs=1e-3), asset


class TestMaxTurnover:
    @pytest.mark.parametrize("objective", EVERY_OBJECTIVE, ids=ids)
    @pytest.mark.parametrize("limit", [0.3, 0.5])  # At 0.1 the 12% target is unreachable and utility holds cash.
    def test_sells_at_most_the_limit(self, objective, limit):
        weights, _ = compute_portfolio(fixture_prices(), objective, current_weights=LOSERS, max_turnover=limit)
        assert sold(LOSERS, weights) <= limit + TOLERANCE
        assert sum(weights.values()) == pytest.approx(1.0)

    def test_the_limit_is_spent_on_the_most_useful_trade(self):
        """Selling the worst holding (AMZN, -10%) to buy the best stock (NVDA, 69%)."""
        weights, _ = compute_portfolio(fixture_prices(), current_weights=LOSERS, max_turnover=0.2)
        assert weights == pytest.approx({"AAPL": 0.5, "AMZN": 0.3, "NVDA": 0.2}, abs=1e-3)

    def test_new_cash_is_invested_without_selling(self):
        current = {"AMZN": 0.3}  # and 70% cash
        weights, _ = compute_portfolio(fixture_prices(), current_weights=current, max_turnover=0.0)
        assert weights["AMZN"] >= 0.3 - TOLERANCE
        assert sum(weights.values()) == pytest.approx(1.0)

    def test_min_volatility_stays_put_with_no_selling_allowed(self):
        weights, _ = compute_portfolio(
            fixture_prices(), Objective(ObjectiveName.MIN_VOLATILITY), current_weights=LOSERS, max_turnover=0.0
        )
        assert weights == pytest.approx(LOSERS, abs=1e-4)

    def test_holdings_outside_the_universe_must_be_sold(self):
        current = {"AAPL": 0.3, "VTI": 0.4}  # VTI is not in the fixture's universe
        with pytest.raises(TurnoverInfeasible) as excinfo:
            compute_portfolio(fixture_prices(), current_weights=current, max_turnover=0.2)
        message = str(excinfo.value)
        assert "selling at least 40.0%" in message
        assert "selling holdings outside the universe: VTI" in message
        assert "Raise max_turnover to at least 0.40, or add VTI to the universe." in message
        weights, _ = compute_portfolio(fixture_prices(), current_weights=current, max_turnover=0.4)  # The hint works.
        assert "VTI" not in weights
        assert sold(current, weights) <= 0.4 + TOLERANCE

    def test_trimming_above_max_weight_counts(self):
        with pytest.raises(TurnoverInfeasible, match="trimming holdings above max_weight: AAPL"):
            compute_portfolio(
                fixture_prices(), current_weights={"AAPL": 0.6, "AMZN": 0.4}, max_turnover=0.05, max_weight=0.4
            )

    def test_max_positions_selling_is_explained(self):
        with pytest.raises(TurnoverInfeasible) as excinfo:
            compute_portfolio(
                fixture_prices(), current_weights={"AAPL": 0.3, "MSFT": 0.3}, max_turnover=0.3, max_positions=1
            )
        message = str(excinfo.value)
        assert "outside the 1 position max_positions kept" in message
        assert "raise max_positions so fewer current holdings have to be sold" in message

    def test_unreachable_targets_mention_the_limit(self):
        with pytest.raises(TargetUnreachable, match="can reach within max_turnover is .* raise max_turnover"):
            compute_portfolio(
                fixture_prices(),
                Objective(ObjectiveName.EFFICIENT_RETURN, target_return=0.3),
                current_weights=LOSERS,
                max_turnover=0.2,
            )
        with pytest.raises(TargetUnreachable, match="can reach within max_turnover"):
            compute_portfolio(
                fixture_prices(),
                Objective(ObjectiveName.EFFICIENT_RISK, target_volatility=0.09),
                current_weights=LOSERS,
                max_turnover=0.1,
            )

    def test_the_highest_return_within_the_limit_is_reachable(self):
        mu, _ = estimate_inputs(fixture_prices())
        highest = highest_net_return(mu, 0.0, 1.0, rebalance(LOSERS, max_turnover=0.2))
        assert highest == pytest.approx(0.5 * mu["AAPL"] + 0.3 * mu["AMZN"] + 0.2 * mu["NVDA"], abs=1e-6)
        _, (ret, _, _) = compute_portfolio(
            fixture_prices(),
            Objective(ObjectiveName.EFFICIENT_RETURN, target_return=highest),
            current_weights=LOSERS,
            max_turnover=0.2,
        )
        assert ret == pytest.approx(highest, abs=TOLERANCE)


class TestCashUnderALimit:
    def test_cash_that_needs_selling_everything_is_blocked_with_a_hint(self):
        with pytest.raises(CashBlockedByTurnover) as excinfo:
            compute_portfolio(fixture_prices(), current_weights=LOSERS, max_turnover=0.2, risk_free_rate=10.0)
        message = str(excinfo.value)
        assert "hold cash" in message
        assert "Moving to cash would sell 100.0% of the portfolio, above max_turnover (0.2)" in message
        assert "Raise max_turnover to at least 1.00" in message
        assert isinstance(excinfo.value.answer, NoAssetBeatsRiskFreeRate)

    def test_cash_within_the_limit_is_an_answer(self):
        with pytest.raises(NoAssetBeatsRiskFreeRate):
            compute_portfolio(fixture_prices(), current_weights=LOSERS, max_turnover=1.0, risk_free_rate=10.0)

    def test_stuck_in_losers_holds_cash_only_if_allowed(self):
        """With no selling allowed, the only reachable portfolio (0.8%) loses to cash, which is out of reach too."""
        with pytest.raises(CashBlockedByTurnover, match="No portfolio reachable from the current holdings"):
            compute_portfolio(fixture_prices(), current_weights=LOSERS, max_turnover=0.0)


class TestTradingCosts:
    @pytest.mark.parametrize(
        "objective",
        # Not min_volatility (ignores return, so costs) nor efficient_return (minimizes volatility; costs enter only
        # its net-return target, so higher costs can mean more trading to reach it).
        [o for o in EVERY_OBJECTIVE if o.name not in (ObjectiveName.MIN_VOLATILITY, ObjectiveName.EFFICIENT_RETURN)],
        ids=ids,
    )
    def test_costs_mean_less_trading(self, objective):
        free, _ = compute_portfolio(fixture_prices(), objective, current_weights=LOSERS, transaction_cost_bps=1)
        costly, _ = compute_portfolio(fixture_prices(), objective, current_weights=LOSERS, transaction_cost_bps=1000)
        assert traded(LOSERS, costly) < traded(LOSERS, free) - 0.01

    def test_min_volatility_ignores_costs(self):
        objective = Objective(ObjectiveName.MIN_VOLATILITY)
        costly, _ = compute_portfolio(fixture_prices(), objective, current_weights=LOSERS, transaction_cost_bps=1000)
        fresh, _ = compute_portfolio(fixture_prices(), objective)
        assert costly == pytest.approx(fresh, abs=1e-4)

    def test_max_sharpe_maximizes_the_sharpe_ratio_net_of_costs(self):
        cost_rate = 0.10  # 1000 bps

        def net_sharpe(weights: dict) -> float:
            mu, cov = estimate_inputs(fixture_prices())
            w = pd.Series(weights).reindex(mu.index, fill_value=0.0).to_numpy()
            net = w @ mu.to_numpy() - cost_rate * traded(LOSERS, weights) - 0.02
            return net / math.sqrt(w @ cov.to_numpy() @ w)

        aware, _ = compute_portfolio(fixture_prices(), current_weights=LOSERS, transaction_cost_bps=1000)
        unaware, _ = compute_portfolio(fixture_prices())
        assert net_sharpe(aware) > net_sharpe(unaware)
        assert net_sharpe(aware) >= max(net_sharpe(unaware), net_sharpe({"NVDA": 1.0}), net_sharpe(LOSERS)) - 1e-6

    def test_efficient_return_target_is_net_of_costs(self):
        weights, (ret, _, _) = compute_portfolio(
            fixture_prices(),
            Objective(ObjectiveName.EFFICIENT_RETURN, target_return=0.3),
            current_weights=LOSERS,
            transaction_cost_bps=1000,
        )
        assert ret - 0.10 * traded(LOSERS, weights) >= 0.3 - TOLERANCE

    def test_costs_can_make_cash_the_answer(self):
        """NVDA's 69% beats a 60% rate, but not after 10% costs on the 200% traded to get there from AMZN."""
        with pytest.raises(NoPortfolioBeatsRiskFreeRate, match="after trading costs \\(best: 49.4%\\)"):
            compute_portfolio(
                fixture_prices(), current_weights={"AMZN": 1.0}, transaction_cost_bps=1000, risk_free_rate=0.6
            )

    def test_cash_comparisons_count_costs(self):
        with pytest.raises(CashBeatsUtility, match="trading costs"):
            compute_portfolio(
                fixture_prices(),
                Objective(ObjectiveName.MAX_UTILITY, risk_aversion=10),
                current_weights={"AMZN": 1.0},
                transaction_cost_bps=1000,
                risk_free_rate=0.4,
            )


class TestTradeList:
    def price_of(self, asset: str) -> float:
        return {"AAPL": 100.0, "MSFT": 50.0, "NVDA": 200.0}[asset]

    def test_sells_out_exactly_and_buys_the_rest(self):
        output = build_portfolio_output({"MSFT": 1.0}, 1000.0, self.price_of, current_shares={"AAPL": 10})
        assert output["trades"]["AAPL"] == {"action": "sell", "shares": 10.0, "value": 1000.0}
        assert output["trades"]["MSFT"] == {"action": "buy", "shares": 20.0, "value": 1000.0}
        assert output["rebalance"] == {"bought": 1000.0, "sold": 1000.0, "turnover": 1.0, "transaction_cost": 0.0}

    def test_fees_are_paid_out_of_the_portfolio(self):
        output = build_portfolio_output(
            {"MSFT": 1.0}, 1000.0, self.price_of, current_shares={"AAPL": 10}, transaction_cost_bps=100
        )
        invested = output["optimized_portfolio"]["MSFT"]["value"]
        cost = output["rebalance"]["transaction_cost"]
        assert invested + cost == pytest.approx(1000.0, abs=0.01)
        assert cost == pytest.approx(0.01 * (1000.0 + invested), abs=0.01)
        assert output["cash"]["value"] == pytest.approx(0.0, abs=0.01)

    def test_hold_cash_sells_everything(self):
        output = build_portfolio_output({}, 2000.0, self.price_of, current_shares={"AAPL": 10, "NVDA": 5})
        assert {asset: trade["action"] for asset, trade in output["trades"].items()} == {
            "AAPL": "sell",
            "NVDA": "sell",
        }
        assert output["cash"] == {"value": 2000.0, "percent_weight": 100.0}

    def test_from_cash_everything_is_a_buy(self):
        output = build_portfolio_output({"AAPL": 0.5, "MSFT": 0.5}, 1000.0, self.price_of)
        assert {trade["action"] for trade in output["trades"].values()} == {"buy"}
        assert output["rebalance"]["turnover"] == 0.0

    def test_unchanged_holdings_are_not_trades(self):
        output = build_portfolio_output({"AAPL": 1.0}, 1000.0, self.price_of, current_shares={"AAPL": 10})
        assert output["trades"] == {}

    def test_fees_come_out_of_the_buys_not_the_kept_positions(self):
        """Regression: scaling the whole portfolio to pay fees made dust sells of every kept position."""
        held = {"AAPL": 600.0, "MSFT": 400.0}
        values, cash, fees = plan_trades({"AAPL": 0.6, "NVDA": 0.4}, 1000.0, held, fee_rate=0.01)
        assert values["AAPL"] == 600.0  # Kept exactly: no trade.
        assert values["MSFT"] == 0.0  # Sold exactly as decided.
        assert values["NVDA"] + fees == pytest.approx(400.0)
        assert fees == pytest.approx(0.01 * (400.0 + values["NVDA"]))
        assert cash == pytest.approx(0.0, abs=1e-9)

    def test_reported_turnover_is_what_the_optimizer_sold(self):
        """Regression: a 25% limit was reported as 25.02% because fees trimmed kept positions."""
        output = build_portfolio_output(
            {"AAPL": 0.75, "NVDA": 0.25},
            2000.0,
            self.price_of,
            current_shares={"AAPL": 15, "MSFT": 10},
            transaction_cost_bps=5,
        )
        assert output["rebalance"]["turnover"] == 0.25
        assert set(output["trades"]) == {"MSFT", "NVDA"}

    def test_solver_noise_is_not_a_trade(self):
        values, _, _ = plan_trades(
            {"AAPL": 0.6000004, "MSFT": 0.3999996}, 100_000.0, {"AAPL": 60_000.0, "MSFT": 40_000.0}
        )
        assert values == {"AAPL": 60_000.0, "MSFT": 40_000.0}

    def test_buys_shrink_when_cash_cannot_cover_them(self):
        values, cash, fees = plan_trades({"AAPL": 1.0}, 1000.0, {}, fee_rate=0.5)
        assert values["AAPL"] * 1.5 == pytest.approx(1000.0)
        assert cash == pytest.approx(0.0, abs=1e-9)
        assert fees == pytest.approx(0.5 * values["AAPL"])


class TestValidation:
    @pytest.mark.parametrize("value", [-0.1, 1.5, True, "0.2", math.inf])
    def test_max_turnover_range(self, value):
        with pytest.raises(InvalidOptimizationInput, match="max_turnover"):
            parse_constraints({"max_turnover": value}, universe_size=5)

    def test_max_turnover_has_constraint_precision(self):
        assert parse_constraints({"max_turnover": 0.123}, universe_size=5).max_turnover == 0.12
        assert parse_constraints({"max_turnover": 0}, universe_size=5).max_turnover == 0.0
        with pytest.raises(InvalidOptimizationInput):
            Constraints(max_turnover=2.0)


class TestOptimizeApi:
    def post(self, client, **body):
        return client.post("/api/optimize/", json={"universe": ["top5"], **body}, headers=HEADERS)

    def test_rebalances_from_holdings_within_the_limit(self, client):
        response = self.post(
            client, initial_holdings={"AAPL": 5, "AMZN": 5}, constraints={"max_turnover": 0.2}, transaction_cost_bps=5
        )
        assert response.status_code == 200, response.get_json()
        data = response.get_json()
        assert data["rebalance"]["turnover"] <= 0.2 + 1e-3
        assert {trade["action"] for asset, trade in data["trades"].items() if asset in ("AAPL", "AMZN")} <= {"sell"}
        assert data["rebalance"]["transaction_cost"] > 0

    def test_a_fresh_request_lists_only_buys(self, client):
        data = self.post(client, initial_cash=1000).get_json()
        assert {trade["action"] for trade in data["trades"].values()} == {"buy"}
        assert data["rebalance"]["sold"] == 0.0

    def test_holdings_outside_the_universe_are_sold(self, client):
        data = self.post(client, universe=["AAPL", "MSFT"], initial_holdings={"NVDA": 1}).get_json()
        assert data["trades"]["NVDA"]["action"] == "sell"
        assert "NVDA" not in data["optimized_portfolio"]

    def test_an_infeasible_limit_is_a_free_400(self, app, client):
        before = uses(app)
        response = self.post(
            client, universe=["AAPL", "MSFT"], initial_holdings={"NVDA": 1}, constraints={"max_turnover": 0.5}
        )
        assert response.status_code == 400
        assert "add NVDA to the universe" in response.get_json()["error"]["message"]
        assert uses(app) == before

    @pytest.mark.parametrize("value", [-1, 1001, True, "5"])
    def test_transaction_cost_bps_is_validated(self, client, value):
        response = self.post(client, initial_cash=1000, transaction_cost_bps=value)
        assert response.status_code == 400
        assert "transaction_cost_bps" in response.get_json()["error"]["message"]


def backtest_config(**overrides) -> BacktestConfig:
    prices = fixture_prices()
    values = {
        "start": datetime.date(2023, 7, 1),
        "end": prices.index[-1].date(),
        "universe": tuple(prices.columns),
        "frequency": Frequency.MONTHLY,
        "lookback_days": 120,
        "execution_lag": 0,  # Trade at the decision's prices, so turnover is exactly what was decided.
        "risk_free_rate": 0.0,
    }
    return BacktestConfig(**{**values, **overrides})


class TestBacktest:
    def test_each_rebalance_after_the_first_sells_at_most_the_limit(self):
        cfg = backtest_config(constraints=Constraints(max_turnover=0.1))
        result = run_backtest(fixture_prices(), cfg, OptimizerStrategy(cfg.constraints))
        assert len(result.rebalances) > 3
        first, *later = result.rebalances
        assert first.turnover == pytest.approx(1.0)  # Buying from cash is not limited.
        for event in later:
            assert event.turnover <= 2 * 0.1 + 1e-3  # Fully invested: bought = sold, two-way = 2 x one-way.

    def test_costs_make_decisions_trade_less(self):
        free = backtest_config(transaction_cost_bps=0)
        costly = backtest_config(transaction_cost_bps=500)
        turnover = {}
        for name, cfg in (("free", free), ("costly", costly)):
            strategy = OptimizerStrategy(cfg.constraints, transaction_cost_bps=cfg.transaction_cost_bps)
            result = run_backtest(fixture_prices(), cfg, strategy)
            turnover[name] = np.mean([event.turnover for event in result.rebalances[1:]])
        assert turnover["costly"] < turnover["free"]

    def test_strategy_sees_the_holdings_at_the_decision(self):
        cfg = backtest_config(constraints=Constraints(max_turnover=0.1))
        seen = []

        class Recording(OptimizerStrategy):
            def observe_holdings(self, current_weights):
                seen.append(dict(current_weights))
                super().observe_holdings(current_weights)

        run_backtest(fixture_prices(), cfg, Recording(cfg.constraints))
        assert seen[0] == {}
        assert all(sum(weights.values()) == pytest.approx(1.0, abs=1e-6) for weights in seen[1:])

    def test_request_and_cli_accept_max_turnover(self, capsys):
        universe = list(fixture_prices().columns)
        request = parse_backtest_request(
            {"start": "2023-07-01", "universe": universe, "constraints": {"max_turnover": 0.25}},
            ticker_universe=universe,
            prices=fixture_prices(),
            limits=BacktestLimits(),
        )
        assert request.config.constraints.max_turnover == 0.25
        code = main(
            ["--start", "2023-07-01", "--universe", "top5", "--lookback-days", "120", "--benchmark",
             "--max-turnover", "0.1", "--cost-bps", "10", "--json", "--no-equity"]
        )  # fmt: skip
        assert code == 0
        data = json.loads(capsys.readouterr().out)
        assert data["config"]["constraints"]["max_turnover"] == 0.1
