"""Tests for the walk-forward backtest script (scripts/backtest.py)."""

import datetime
import importlib.util
import pathlib

SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "backtest.py"
spec = importlib.util.spec_from_file_location("backtest", SCRIPT)
backtest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backtest)


def test_next_trading_day_skips_non_trading_days(market):
    index = market.prices.index
    # 2020-07-04 was a Saturday; the next trading day is Monday 2020-07-06
    assert backtest.next_trading_day(index, "2020-07-03", 1) == "2020-07-06"
    assert backtest.next_trading_day(index, "2025-01-01", 1) is None  # past the end of the data


def test_run_backtest_walks_forward(market):
    values = backtest.run_backtest(market, "2020-06-01", 91, initial_cash=10000)
    dates = list(values)
    assert len(dates) >= 3
    assert dates == sorted(dates)
    assert values[dates[0]] == 10000
    assert all(v > 0 for v in values.values())
    # dates step forward by at least the frequency
    gaps = [(datetime.date.fromisoformat(b) - datetime.date.fromisoformat(a)).days for a, b in zip(dates, dates[1:])]
    assert all(gap >= 91 for gap in gaps)


def test_run_backtest_leaves_market_untouched(market):
    backtest.run_backtest(market, "2020-06-01", 182)
    assert len(market.prices) == 400  # truncation happens on copies, never in place


def test_report_warns_about_survivorship_bias_before_and_after_the_numbers(market):
    """The README says the numbers are flattering; the output a reader actually looks at must say so too."""
    lines = []
    assert backtest.main(market, frequencies={"quarterly": 91}, out=lines.append, start_date="2020-06-01") == 0
    text = "\n".join(lines)
    assert text.startswith("WARNING: survivorship-biased")
    assert "TOTAL RETURN" in text and "(survivorship-biased)" in text
    assert lines[-1] == backtest.SURVIVORSHIP_WARNING, "the warning closes the report so it cannot scroll away"
    assert text.count(backtest.SURVIVORSHIP_WARNING) == 2
