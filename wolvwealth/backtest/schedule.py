"""Rebalance scheduling on a trading calendar."""

import pandas as pd

from wolvwealth.backtest.config import Frequency


def rebalance_dates(trading_days: pd.DatetimeIndex, frequency: Frequency) -> list[pd.Timestamp]:
    """Decision dates: the first trading day, then every `frequency` step after it.

    Steps are measured from the first day with calendar arithmetic (k * offset)
    rather than accumulated, so "monthly" from Jan 31 lands on Feb 28, Mar 31,
    ... instead of drifting. Each step is snapped forward to the next trading
    day; duplicates (e.g. daily steps over a weekend) are dropped.
    """
    if len(trading_days) == 0:
        return []
    anchor = trading_days[0]
    dates: list[pd.Timestamp] = []
    k = 0
    while True:
        target = anchor + k * frequency.offset if k else anchor
        position = trading_days.searchsorted(target)
        if position >= len(trading_days):
            break
        candidate = trading_days[position]
        if not dates or candidate != dates[-1]:
            dates.append(candidate)
        k += 1
    return dates
