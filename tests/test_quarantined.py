"""Quarantined tickers: implausible downloaded prices, excluded from allocations.

The archive keeps every close on disk and the snapshot blanks the ticker, so
nothing recent can be traded -- but a position someone already holds is still
a fact. It used to fail the whole request with "No price ever recorded", even
though the history sat on disk; now it is valued at its last believable close
(the newest one before the implausible move) and kept frozen, the same
treatment a delisted or short-history holding gets.
"""

import pytest

from conftest import synthetic_market
from wolvwealth.domain import optimizer
from wolvwealth.domain.errors import ValidationError
from wolvwealth.domain.market import last_believable_close
from wolvwealth.services import market_data
from wolvwealth.services.market_data import MarketDataService
from wolvwealth.storage.price_store import PriceStore


@pytest.fixture
def quarantined(services):
    """The service after a full refresh whose download spiked DDD 40x, quarantining it. Returns (services, prices)."""
    spiked = synthetic_market().prices
    spiked.iloc[100, spiked.columns.get_loc("DDD")] *= 40
    services.market.downloader = lambda tickers, _start, **_k: spiked.loc[:, list(tickers)]
    assert services.market.refresh(full=True)
    return services, spiked


def test_last_believable_close_stops_before_the_first_implausible_move():
    series = synthetic_market().prices["AAA"].copy()
    assert last_believable_close(series) == (float(series.iloc[-1]), series.index[-1].date())
    series.iloc[100] *= 40
    assert last_believable_close(series) == (float(series.iloc[99]), series.index[99].date())
    assert last_believable_close(series * float("nan")) is None, "a history with no close has no valuation"


def test_a_quarantined_ticker_is_blanked_from_the_snapshot_but_keeps_a_frozen_valuation(quarantined):
    services, spiked = quarantined
    data = services.market.data
    assert "x in one day" in services.market.quarantine["DDD"]["reason"]
    assert data.prices["DDD"].isna().all() and "DDD" not in data.tradeable, "unbuyable, with nothing for the solver"
    assert services.price_store.load_prices()["DDD"].notna().all(), "the archive keeps every close"
    close, day = data.frozen_valuations["DDD"]
    assert (close, day) == (float(spiked["DDD"].iloc[99]), spiked.index[99].date()), "the close before the spike"
    assert data.can_value("DDD") and data.valuation_price("DDD") == close and data.valuation_date("DDD") == day


def test_loading_from_disk_rebuilds_the_frozen_valuations_from_the_archive(tmp_path):
    """A web or MCP server that (re)loads the cache must reach the same valuations the refresh published."""
    market = synthetic_market()
    spiked = market.prices.copy()
    spiked.iloc[100, spiked.columns.get_loc("DDD")] *= 40
    store = PriceStore(tmp_path / "historical_prices.parquet", tmp_path / "universe.csv")
    store.save_universe(list(market.universe))
    store.save_prices(spiked)
    store.save_quarantine({"DDD": {"reason": "moved 40.00x in one day", "detected": "2026-11-06"}})
    service = MarketDataService(store, "2020-01-01", downloader=lambda *_a, **_k: pytest.fail(), log=lambda *_: None)
    assert service.data.prices["DDD"].isna().all()
    assert service.data.frozen_valuations["DDD"] == (float(spiked["DDD"].iloc[99]), spiked.index[99].date())


def test_a_quarantined_holding_is_kept_frozen_instead_of_refusing_the_request(quarantined):
    """Refusing the whole optimization over one quarantined holding breaks unattended runs.

    Regression: a scheduled MCP run got "No price ever recorded for DDD ... run refresh-data --full"
    while DDD's entire history sat on disk and --full would simply re-quarantine it.
    """
    services, _ = quarantined
    data = services.market.data
    # DDD named in the universe while held is skipped, not refused: every surface names held tickers.
    body = {"initial_cash": 1000, "initial_holdings": {"DDD": 5}, "universe": ["AAA", "BBB", "DDD"]}
    result = optimizer.optimize(body, data)
    kept = result["unoptimized"]["positions"]["DDD"]
    assert kept["reason"] == "quarantined"
    assert kept["priced_as_of"] == str(data.valuation_date("DDD")), "the assumed price's date is stated"
    assert result["optimized_portfolio"]["DDD"]["shares"] == 5, "kept exactly as held"
    assert not any(trade["ticker"] == "DDD" for trade in result["trades"]), "never traded at a distrusted price"
    assert "quarantined" in result["unoptimized"]["note"] and "believable close" in result["unoptimized"]["note"]
    assert "DDD" in result["stale_prices"]
    assert set(result["optimized_portfolio"]) - {"DDD"}, "the money that can be optimized still is"


def test_naming_a_quarantined_ticker_in_the_universe_without_holding_it_says_why(quarantined):
    services, _ = quarantined
    with pytest.raises(ValidationError) as excinfo:
        optimizer.optimize({"initial_cash": 1000, "universe": ["AAA", "DDD"]}, services.market.data)
    assert "DDD is quarantined" in excinfo.value.message and "plausibility" in excinfo.value.message


def test_api_serves_a_plan_for_a_quarantined_holding_instead_of_an_error(client, quarantined):
    services, _ = quarantined
    response = client.post("/api/optimize/", json={"initial_cash": 1000, "initial_holdings": {"DDD": 1}})
    assert response.status_code == 200
    body = response.get_json()
    assert body["unoptimized"]["positions"]["DDD"]["reason"] == "quarantined"
    assert body["stale_prices"]["DDD"]["as_of"] == str(services.market.data.valuation_date("DDD"))


def test_classify_tickers_gives_quarantined_names_their_own_bucket(quarantined):
    services, _ = quarantined
    report = market_data.classify_tickers(services.market.data, ["aaa", "ddd", "zzz"])
    assert report["quarantined"] == ["DDD"] and report["no_current_price"] == []
    assert report["tradeable"] == ["AAA"] and report["unknown"] == ["ZZZ"]
