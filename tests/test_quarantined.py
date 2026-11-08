"""Quarantined tickers: implausible downloaded prices, excluded from allocations.

The archive keeps every close on disk and the snapshot blanks the ticker, so
nothing recent can be traded -- but a position someone already holds is still
a fact. It used to fail the whole request with "No price ever recorded", even
though the history sat on disk; now it is valued at its last believable close
(the newest one before the implausible move) and kept frozen, the same
treatment a delisted or short-history holding gets.
"""

import pandas as pd
import pytest

from conftest import synthetic_market
from test_market_data import wide_market_prices
from test_split_repair import build_service as split_service
from test_split_repair import splitting_downloader
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
    assert "quarantined" in result["unoptimized"]["note"] and "could be trusted" in result["unoptimized"]["note"]
    assert "DDD" in result["stale_prices"]
    assert set(result["optimized_portfolio"]) - {"DDD"}, "the money that can be optimized still is"


@pytest.mark.parametrize("how", ["fail_repair", "truncate_repair"], ids=["empty repair", "truncated repair"])
@pytest.mark.parametrize("ratio", [0.5, 0.1, 10.0], ids=["2:1", "10:1", "1:10 reverse"])
def test_a_failed_split_repair_values_the_holding_on_the_new_basis_not_the_cached_one(tmp_path, how, ratio):
    """The cached history is on the superseded basis: after a 2:1 split its closes are twice the real price.

    Regression: freezing such a holding at its last *cached* close valued the position at the
    pre-split price, so a 2:1 split reported the shares as worth double. The download that
    detected the re-basing is on the current basis even when the repair itself came back short.
    """
    service, store = split_service(tmp_path)
    download, full, _calls = splitting_downloader(service, ratio=ratio, **{how: True})
    service.downloader = download
    assert service.refresh() is True

    data = service.data
    assert "adjustment basis" in service.quarantine["BBB"]["reason"]
    assert data.prices["BBB"].isna().all(), "still unbuyable: a stub history is no basis for statistics"
    # The frozen valuation is a real close on the basis Yahoo serves now...
    new_basis = full["BBB"].dropna().iloc[-1]
    assert data.valuation_price("BBB") == pytest.approx(new_basis)
    # ...and emphatically not the cached one, which is the whole bug: the two differ by the
    # split ratio (give or take the few days of drift the fixture adds past the split).
    cached_close = store.load_prices()["BBB"].dropna().iloc[-1]
    assert data.valuation_price("BBB") / cached_close == pytest.approx(ratio, rel=0.05)
    # So the position is worth what it is worth, not ratio-times that.
    assert data.value({"BBB": 10}) == pytest.approx(10 * new_basis)


def test_a_valuation_recorded_for_a_failed_repair_survives_later_refreshes_that_see_nothing(tmp_path):
    """Once the overlap moves past the split, a retry download holds nothing new to value from.

    The recorded current-basis close must be carried forward rather than lost, or the holding
    would silently fall back to the pre-split cached close after a few days of retries.
    """
    service, store = split_service(tmp_path)
    download, full, _calls = splitting_downloader(service, fail_repair=True)
    service.downloader = download
    assert service.refresh() is True
    recorded = store.load_quarantine()["BBB"]["valuation"]
    assert recorded["close"] == pytest.approx(full["BBB"].dropna().iloc[-1])

    # Later refreshes: BBB is dark everywhere (still throttled), so nothing new can be observed.
    served = store.load_prices()
    extra = pd.bdate_range(served.index[-1] + pd.Timedelta(days=1), periods=3)
    rows = pd.DataFrame({t: [served[t].dropna().iloc[-1]] * len(extra) for t in served.columns}, index=extra)
    rows["BBB"] = float("nan")

    def dark_for_bbb(tickers, start, **_kwargs):
        frame = pd.concat([served.loc[served.index >= start], rows]).loc[:, list(tickers)].copy()
        frame["BBB"] = float("nan")
        return frame

    service.downloader = dark_for_bbb
    service.refresh()
    assert store.load_quarantine()["BBB"]["valuation"] == recorded, "carried forward, not dropped or re-derived"
    assert service.data.valuation_price("BBB") == pytest.approx(recorded["close"])


def test_a_full_refresh_also_values_a_failed_re_basing_on_the_new_basis(tmp_path):
    """The full-refresh path reaches the same conclusion by its own route (`_merge_full_download`)."""
    tickers = [f"TK{i}" for i in range(8)]
    cached = wide_market_prices(tickers)
    store = PriceStore(tmp_path / "historical_prices.parquet", tmp_path / "universe.csv")
    store.save_universe(tickers)
    store.save_prices(cached)
    full = cached.copy()
    full["TK1"] = (full["TK1"] * 0.5).round(2)  # 2:1 split, and only the recent tail comes back
    full["TK1"] = full["TK1"].where(full.index >= full.index[-60])

    service = MarketDataService(
        store, "2020-01-01", downloader=lambda req, _s, **_k: full.loc[:, list(req)], log=lambda *_: None
    )
    assert service.refresh(full=True) is True
    assert "adjustment basis" in service.quarantine["TK1"]["reason"]
    assert service.data.valuation_price("TK1") == pytest.approx(full["TK1"].dropna().iloc[-1])
    assert service.data.valuation_price("TK1") == pytest.approx(cached["TK1"].iloc[-1] * 0.5, rel=0.01)


def test_an_implausible_move_still_values_from_the_cache_since_the_download_is_what_is_distrusted(quarantined):
    """The two reasons must not be conflated: here the fresh prices are the suspect ones."""
    services, spiked = quarantined
    data = services.market.data
    assert "valuation" not in services.market.quarantine["DDD"], "no fresh close is trusted for a spiked ticker"
    assert data.valuation_price("DDD") == pytest.approx(float(spiked["DDD"].iloc[99]))


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
