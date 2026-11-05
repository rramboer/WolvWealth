"""Expected-return estimators: the properties the optimizer and its precomputed cache rely on."""

import numpy as np
import pandas as pd
import pytest

from conftest import synthetic_market
from wolvwealth.domain import stats
from wolvwealth.domain.market import MarketData
from wolvwealth.domain.stats import MarketStats


@pytest.mark.parametrize("name", sorted(stats.ESTIMATORS))
def test_every_estimator_is_separable_per_ticker(name):
    """The cache is computed over 500 names and sliced per request; that is only valid if mu is per column."""
    prices = synthetic_market().prices
    full = stats.expected_return(prices, name)
    subset = stats.expected_return(prices[["CCC", "AAA"]], name)
    assert np.allclose(full[["CCC", "AAA"]], subset)
    assert np.isfinite(full).all()


@pytest.mark.parametrize("name", sorted(stats.ESTIMATORS))
def test_every_estimator_tolerates_a_ticker_with_a_short_history(name):
    """A name that listed recently (leading NaNs) gets a finite estimate and does not poison its neighbours."""
    prices = synthetic_market().prices.copy()
    prices.loc[prices.index[:300], "DDD"] = np.nan
    mu = stats.expected_return(prices, name)
    assert np.isfinite(mu).all()
    assert np.allclose(
        mu[["AAA", "BBB", "CCC"]], stats.expected_return(synthetic_market().prices, name)[["AAA", "BBB", "CCC"]]
    )


def test_recency_weighted_estimators_follow_the_recent_past_and_the_full_history_mean_does_not():
    """Pins down what each name means: a stock that rallied early and went flat lately."""
    dates = pd.bdate_range("2015-01-02", periods=2000)
    early_rally = np.concatenate([np.linspace(100, 400, 500), np.full(1500, 400.0)])
    prices = pd.DataFrame({"X": early_rally}, index=dates)
    estimates = {name: float(stats.expected_return(prices, name)["X"]) for name in stats.ESTIMATORS}
    assert estimates["mean_historical"] > 0.15, "the whole-history CAGR still remembers the rally"
    assert estimates["mean_5y"] == pytest.approx(0.0, abs=1e-9), "the last five years were flat"
    assert estimates["ema_250"] < estimates["ema_500"] < estimates["ema_1000"] < estimates["mean_historical"]
    assert estimates["ema_250"] == pytest.approx(0.0, abs=1e-3)


def test_unknown_estimator_names_are_rejected_everywhere():
    prices = synthetic_market().prices
    with pytest.raises(ValueError, match="Unknown expected-return estimator"):
        stats.expected_return(prices, "oracle")
    with pytest.raises(ValueError, match="Unknown expected-return estimator"):
        MarketData.build(["AAA"], prices, estimator="oracle")
    with pytest.raises(ValueError, match="computed with"):
        MarketData.build(["AAA"], prices, MarketStats.compute(prices, "ema_500"), estimator="mean_historical")


def test_with_estimator_starts_from_nothing_and_changes_the_optimizer_inputs(market):
    cached = MarketData.build(market.universe, market.prices, MarketStats.compute(market.prices))
    cached.solutions.get_or_compute(("probe",), lambda: "memo")
    other = cached.with_estimator("mean_historical")
    assert other.estimator == "mean_historical" and other.stats is None
    assert other.solutions.get_or_compute(("probe",), lambda: "fresh") == "fresh", "no memo carried across estimators"
    assert other.truncated(market.prices.index[200]).estimator == "mean_historical", "truncation keeps the choice"
    mu_default, cov_default = cached.stats_for(["AAA", "BBB"])
    mu_other, cov_other = other.stats_for(["AAA", "BBB"])
    assert not np.allclose(mu_default, mu_other) and np.allclose(cov_default, cov_other)
