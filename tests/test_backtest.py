"""Tests for the walk-forward backtest script (scripts/backtest.py)."""

import datetime
import importlib.util
import pathlib

import numpy as np
import pandas as pd
import pytest

SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "backtest.py"
spec = importlib.util.spec_from_file_location("backtest", SCRIPT)
backtest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backtest)

#: The synthetic market starts 2020-01-02; a year of history exists from here on.
START = "2021-01-04"


def test_next_trading_day_skips_non_trading_days(market):
    index = market.prices.index
    # 2020-07-04 was a Saturday; the next trading day is Monday 2020-07-06
    assert backtest.next_trading_day(index, "2020-07-03", 1) == "2020-07-06"
    assert backtest.next_trading_day(index, "2025-01-01", 1) is None  # past the end of the data


@pytest.mark.parametrize("strategy", backtest.STRATEGIES)
def test_run_backtest_values_the_held_shares_daily_between_rebalances(market, strategy):
    walk = backtest.run_backtest(market, START, 60, initial_cash=10000, strategy=strategy)
    assert walk.rebalances == sorted(walk.rebalances) and len(walk.rebalances) >= 3
    gaps = [
        (datetime.date.fromisoformat(b) - datetime.date.fromisoformat(a)).days
        for a, b in zip(walk.rebalances, walk.rebalances[1:])
    ]
    assert all(gap >= 60 for gap in gaps)
    assert walk.values.index[0] == pd.Timestamp(walk.rebalances[0]) and walk.values.index[-1] == market.prices.index[-1]
    assert walk.values.index.is_monotonic_increasing and walk.values.index.is_unique
    # Within rounding: max-Sharpe drops dust weights and rounds shares, so a hair of cash goes unspent.
    assert walk.values.iloc[0] == pytest.approx(10000, rel=1e-3)
    assert (walk.values > 0).all()
    assert len(walk.values) == len(market.prices.loc[walk.rebalances[0] :]), "one value per trading day held"
    assert len(walk.turnovers) == len(walk.rebalances) - 1 and all(0 <= t <= 1 for t in walk.turnovers)


def test_equal_weight_holds_the_universe_equally_at_each_rebalance(market):
    shares = backtest.target_shares(market, START, 10000, {}, "equal_weight")
    known = market.truncated(START)
    values = {ticker: count * known.price(ticker) for ticker, count in shares.items()}
    assert set(shares) == set(backtest.seasoned_universe(known)) == set(market.universe)
    assert np.allclose(list(values.values()), 10000 / len(values))
    with pytest.raises(ValueError, match="unknown strategy"):
        backtest.target_shares(market, START, 10000, {}, "coin_flip")


def test_universe_at_a_date_excludes_names_without_a_year_of_history(market):
    """A name that listed last month is 'top 50' by today's cap but had no statistics at the time."""
    prices = market.prices.copy()
    prices.loc[prices.index[:-100], "DDD"] = np.nan  # DDD listed 100 trading days before the end
    recent_listing = market.__class__.build(market.universe, prices)
    assert backtest.seasoned_universe(recent_listing) == ["AAA", "BBB", "CCC"]
    assert "DDD" in recent_listing.tradeable, "the app itself would still consider it tradeable"
    with pytest.raises(ValueError, match="start the walk later"):
        backtest.seasoned_universe(market.truncated("2020-06-01"))


def test_turnover_is_the_value_sold_as_a_fraction_of_the_portfolio():
    prices = pd.Series({"AAA": 10.0, "BBB": 20.0})
    assert backtest.turnover({"AAA": 10, "BBB": 5}, {"AAA": 4, "BBB": 8}, prices) == pytest.approx(60 / 200)
    assert backtest.turnover({"AAA": 10}, {"AAA": 10}, prices) == 0.0
    assert backtest.turnover({}, {"AAA": 10}, prices) == 0.0, "buying from cash sells nothing"


def test_run_backtest_leaves_market_untouched(market):
    backtest.run_backtest(market, START, 182)
    assert len(market.prices) == 400  # truncation happens on copies, never in place


def test_metrics_of_a_known_series():
    dates = pd.date_range("2020-01-01", "2022-01-01")  # exactly two years, calendar days
    # Quadruples over two years at a constant daily rate, with one 10% dip half-way through.
    values = pd.Series(np.geomspace(100, 400, len(dates)), index=dates)
    middle = len(dates) // 2
    dip_from_previous_peak = 0.9 * values.iloc[middle] / values.iloc[middle - 1] - 1
    values.iloc[middle] *= 0.9
    figures = backtest.metrics(values)
    assert figures["total_return"] == pytest.approx(3.0)
    years = (dates[-1] - dates[0]).days / 365.25
    assert figures["cagr"] == pytest.approx(4 ** (1 / years) - 1, abs=1e-9)
    assert figures["max_drawdown"] == pytest.approx(dip_from_previous_peak, abs=1e-9)
    assert figures["volatility"] > 0 and figures["sharpe"] > 0
    assert figures["turnover"] == 0.0
    assert backtest.metrics(values, turnovers=[0.1, 0.3])["turnover"] == pytest.approx(0.2)
    flat = backtest.metrics(pd.Series([100.0] * 10, index=dates[:10]))
    assert flat["sharpe"] is None and flat["volatility"] == 0 and flat["max_drawdown"] == 0


def test_report_compares_strategies_under_the_chosen_estimator_and_warns_twice(market):
    """The README says the numbers are flattering; the output a reader actually looks at must say so too."""
    lines = []
    argv = ["--estimator", "ema_500", "--start", START, "--frequencies", "quarterly", "--verbose"]
    assert backtest.main(argv, market=market, out=lines.append) == 0
    text = "\n".join(lines)
    assert text.startswith("WARNING: survivorship-biased") and lines[-1] == backtest.SURVIVORSHIP_WARNING
    assert "expected returns: ema_500" in text
    assert "  max_sharpe " in text and "  equal_weight " in text and "Sharpe" in text and "turnover" in text
    assert f"    {START}: 100000.00" in text, "--verbose lists the value at each rebalance"
    assert text.count("(survivorship-biased") == 1
    quiet = []
    assert backtest.main(argv[:-1], market=market, out=quiet.append) == 0
    assert not any(line.startswith(f"    {START}:") for line in quiet), "without --verbose, no per-rebalance lines"
    for bad in (
        ["--estimator", "oracle"],
        ["--start", "last tuesday"],
        ["--frequencies", "hourly"],
        ["--start", "2030-01-01"],  # after the data
        ["--start", "2020-02-03"],  # before any name has a year of history
    ):
        with pytest.raises(SystemExit) as exit_info:
            backtest.main(bad, market=market, out=lines.append)
        assert exit_info.value.code == 2, bad
