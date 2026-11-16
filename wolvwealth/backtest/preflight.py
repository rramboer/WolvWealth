"""Pre-run checks: can this backtest actually trade? Answered without running the optimizer.

Walks the rebalance schedule with the engine's own decision iterator and
skip rules, so a request that would only ever hold cash, for example a start
date with no lookback history yet, is rejected before the caller is charged.
"""

import dataclasses

import pandas as pd

from wolvwealth.backtest.config import BacktestConfig
from wolvwealth.backtest.engine import MIN_WINDOW_ROWS, iter_decisions, prepare
from wolvwealth.backtest.schedule import rebalance_dates
from wolvwealth.optimizer_params import InvalidOptimizationInput, feasibility_problem


@dataclasses.dataclass(frozen=True)
class Preflight:
    """How many scheduled rebalances could execute, and why the others could not."""

    decisions: int
    executable: int
    no_history: int  # Too few observations or no eligible asset in the lookback window.
    infeasible: int  # Eligible assets exist but the constraints cannot fully invest them.
    after_end: int  # The execution lag pushes the trade past the end date.
    max_eligible: int  # Most eligible assets seen at any decision with enough history.
    first_executable: pd.Timestamp | None


def preflight(prices: pd.DataFrame, config: BacktestConfig) -> Preflight:
    """Classify every scheduled decision. Raises ValueError if the range has no usable trading days."""
    raw, trading_days = prepare(prices, config)
    dates = rebalance_dates(trading_days, config.frequency)
    counts = {"executable": 0, "no_history": 0, "infeasible": 0, "after_end": 0}
    max_eligible = 0
    first_executable = None
    for index, decision in enumerate(iter_decisions(raw, trading_days, dates, config)):
        if decision.execution_position is None:
            counts["after_end"] = len(dates) - index  # Every later decision is past the end too.
            break
        if not decision.has_history:
            counts["no_history"] += 1
            continue
        max_eligible = max(max_eligible, len(decision.eligible))
        if feasibility_problem(config.constraints, len(decision.eligible)):
            counts["infeasible"] += 1
            continue
        counts["executable"] += 1
        first_executable = first_executable or decision.date
    return Preflight(decisions=len(dates), max_eligible=max_eligible, first_executable=first_executable, **counts)


def check_runnable(prices: pd.DataFrame, config: BacktestConfig, max_rebalances: int | None = None) -> Preflight:
    """Raise InvalidOptimizationInput unless the backtest is within the data, within limits, and can trade."""
    first, last = prices.index[0].date(), prices.index[-1].date()
    if config.start < first:
        raise InvalidOptimizationInput(f"start is before the first available price date ({first}).")
    if config.end > last:
        raise InvalidOptimizationInput(f"end is after the last available price date ({last}).")
    try:
        plan = preflight(prices, config)
    except ValueError as err:
        raise InvalidOptimizationInput(str(err)) from err
    if max_rebalances is not None and plan.decisions > max_rebalances:
        raise InvalidOptimizationInput(
            f"Too many rebalances: this request needs {plan.decisions}, the limit is {max_rebalances}. "
            "Shorten the date range or choose a lower frequency."
        )
    if plan.executable == 0:
        raise InvalidOptimizationInput(f"No rebalance could execute in this range: {_reason(plan, config, first)}")
    return plan


def _reason(plan: Preflight, config: BacktestConfig, first_price_date) -> str:
    if plan.decisions == 0 or plan.after_end == plan.decisions:
        return (
            f"the range is too short for a {config.execution_lag}-trading-day execution lag. "
            "Extend the end date or lower the lag."
        )
    if plan.infeasible >= plan.no_history:
        return (
            f"at most {plan.max_eligible} stock(s) had enough price history, which these constraints cannot fully "
            "invest. Raise max_weight, lower min_weight, or add stocks."
        )
    return (
        f"there is not enough price history before {config.start} (prices begin {first_price_date}; each rebalance "
        f"needs at least {MIN_WINDOW_ROWS} trading days in its lookback window). Start later."
    )
