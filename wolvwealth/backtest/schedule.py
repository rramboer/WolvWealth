"""Rebalance scheduling on a trading calendar."""

import numpy as np
import pandas as pd

from wolvwealth.backtest.config import Frequency

_DAY_BASED = {Frequency.DAILY: 1, Frequency.WEEKLY: 7, Frequency.BIWEEKLY: 14}


def rebalance_dates(trading_days: pd.DatetimeIndex, frequency: Frequency) -> list[pd.Timestamp]:
    """Decision dates: the first trading day, then every `frequency` step after it.

    Steps are measured from the first day with calendar arithmetic (k * offset)
    rather than accumulated, so "monthly" from Jan 31 lands on Feb 28, Mar 31,
    ... instead of drifting. Each step is snapped forward to the next trading
    day; duplicates (e.g. daily steps over a weekend) are dropped.

    Day-based frequencies are generated in one vectorized step, so a daily
    schedule over the whole history costs microseconds, not a Python loop
    over thousands of DateOffset additions.
    """
    if len(trading_days) == 0:
        return []
    anchor, last = trading_days[0], trading_days[-1]
    if frequency in _DAY_BASED:
        step = _DAY_BASED[frequency]
        count = (last - anchor).days // step + 1
        targets = pd.DatetimeIndex(anchor + pd.to_timedelta(np.arange(count) * step, unit="D"))
    else:
        targets = []
        k = 0
        while True:
            target = anchor + k * frequency.offset if k else anchor
            if target > last:
                break
            targets.append(target)
            k += 1
        targets = pd.DatetimeIndex(targets)
    positions = np.unique(trading_days.searchsorted(targets))
    positions = positions[positions < len(trading_days)]
    return [trading_days[position] for position in positions]
