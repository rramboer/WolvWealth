"""The simulation loop: turns a strategy's decisions into a daily equity curve.

Accounting rules
----------------
* On each decision date the strategy sees prices up to and including that
  day, over `config.lookback_days` of history, for the *eligible* assets only
  (those that already traded at the start of the window and still trade now).
* Trades happen `config.execution_lag` trading days later at that day's close.
  The lag is what prevents the optimizer from buying at the same price it
  just observed.
* Transaction costs are charged on traded value (sells and buys). The amount
  invested is solved so that costs are paid out of the portfolio and cash
  never goes negative.
* Share counts and cash are carried as exact floats; rounding is a display
  concern. Cash left over (weights summing to less than one) stays as cash
  and is counted in the portfolio value.
* Cash earns the configured risk-free rate, compounded over calendar days
  between trading days. The same rate drives the hold-cash decision and the
  Sharpe ratio, so a portfolio that sits in cash is valued the way the model
  assumed when it chose to.
* The equity curve is marked to market every trading day from `start` to
  `end`, including the days after the final rebalance.
"""

import dataclasses
from collections.abc import Callable, Iterator

import numpy as np
import pandas as pd

from wolvwealth.backtest.config import BacktestConfig
from wolvwealth.backtest.results import BacktestResult, RebalanceEvent
from wolvwealth.backtest.schedule import rebalance_dates
from wolvwealth.backtest.strategies import Strategy, StrategyError, Weights

MIN_WINDOW_ROWS = 20  # Fewer observations than this and the window is "insufficient history".
MAX_MISSING_FRACTION = 0.1  # Assets missing more than this share of the window's prices are skipped.


StepCallback = Callable[[int, int], None]  # (decisions completed, total decisions)


@dataclasses.dataclass(frozen=True)
class Decision:
    """One scheduled rebalance decision and the data the strategy would see."""

    date: pd.Timestamp
    execution_position: int | None  # Trading-day index of the trade; None if it would fall after `end`.
    window: pd.DataFrame  # Lookback prices for the whole universe, up to and including `date`.
    eligible: list[str]  # Universe members tradable on this date (see eligible_assets).

    @property
    def has_history(self) -> bool:
        """Enough observations and at least one eligible asset to optimize over."""
        return len(self.window) >= MIN_WINDOW_ROWS and bool(self.eligible)


def trading_days_in_range(prices: pd.DataFrame, config: BacktestConfig) -> pd.DatetimeIndex:
    """Trading days in [start, end]. Index-only, so it is cheap. Raises ValueError if fewer than two."""
    index = prices.index if prices.index.is_monotonic_increasing else prices.index.sort_values()
    start, end = pd.Timestamp(config.start), pd.Timestamp(config.end)
    trading_days = index[(index >= start) & (index <= end)]
    if len(trading_days) < 2:
        raise ValueError(f"Fewer than two trading days of price data between {config.start} and {config.end}.")
    return trading_days


def universe_prices(prices: pd.DataFrame, config: BacktestConfig) -> pd.DataFrame:
    """Price columns for the universe, sorted by date. Raises ValueError for unknown tickers."""
    missing = [ticker for ticker in config.universe if ticker not in prices.columns]
    if missing:
        raise ValueError(f"No price data available for: {', '.join(sorted(missing))}.")
    return prices[list(config.universe)].sort_index()


def prepare(prices: pd.DataFrame, config: BacktestConfig) -> tuple[pd.DataFrame, pd.DatetimeIndex]:
    """Universe prices and the trading days in [start, end]. Raises ValueError if unusable."""
    return universe_prices(prices, config), trading_days_in_range(prices, config)


def iter_decisions(
    raw: pd.DataFrame, trading_days: pd.DatetimeIndex, dates: list[pd.Timestamp], config: BacktestConfig
) -> Iterator[Decision]:
    """Yield each scheduled decision with its lookback window and eligible assets.

    The single source of truth for which decisions can trade: run_backtest
    and the pre-run check (wolvwealth.backtest.preflight) both consume it.
    """
    for date in dates:
        execution_position = trading_days.get_loc(date) + config.execution_lag
        window = raw.loc[date - config.lookback : date]
        yield Decision(
            date=date,
            execution_position=execution_position if execution_position < len(trading_days) else None,
            window=window,
            eligible=eligible_assets(window),
        )


def run_backtest(
    prices: pd.DataFrame,
    config: BacktestConfig,
    strategy: Strategy,
    on_step: StepCallback | None = None,
) -> BacktestResult:
    """Simulate `strategy` on `prices` according to `config`.

    `on_step(completed, total)` is called before each rebalance decision and
    once more when the simulation finishes, so callers can report progress.
    """
    raw, trading_days = prepare(prices, config)
    universe = list(config.universe)
    filled = raw.ffill()  # Valuation prices: carry the last known price across gaps.
    valuation = filled.loc[trading_days].fillna(0.0)  # Unheld assets without a price contribute nothing.
    fee_rate = config.transaction_cost_bps / 10_000

    ledger = _Ledger(cash=config.initial_cash, shares=pd.Series(0.0, index=universe))
    growth = cash_growth_factors(trading_days, config.risk_free_rate)
    equity = pd.Series(index=trading_days, dtype=float)
    events: list[RebalanceEvent] = []
    warnings: list[str] = []
    cursor = 0  # First trading-day position whose equity has not been written yet.

    dates = rebalance_dates(trading_days, config.frequency)
    for completed, decision in enumerate(iter_decisions(raw, trading_days, dates, config)):
        if on_step is not None:
            on_step(completed, len(dates))
        if decision.execution_position is None:
            break
        if not decision.has_history:
            warnings.append(f"{decision.date.date()}: insufficient price history; holding previous positions.")
            continue
        try:
            weights = strategy.target_weights(decision.window[decision.eligible].ffill())
        except StrategyError as err:
            warnings.append(f"{decision.date.date()}: {err}; holding previous positions.")
            continue
        if weights is None:
            continue

        execution_position = decision.execution_position
        prices_now = valuation.iloc[execution_position]
        untradable = [asset for asset in weights if prices_now.get(asset, 0.0) <= 0]
        if untradable:
            warnings.append(f"{decision.date.date()}: no execution price for {', '.join(untradable)}; holding.")
            continue
        _write_equity(equity, valuation, ledger, growth, cursor, execution_position)
        ledger.accrue(growth[execution_position] / growth[cursor])
        cursor = execution_position
        events.append(
            ledger.rebalance(
                weights,
                prices_now=prices_now,
                fee_rate=fee_rate,
                decision_date=decision.date,
                execution_date=trading_days[execution_position],
                eligible_count=len(decision.eligible),
                expected_performance=getattr(strategy, "last_performance", None),
                note=getattr(strategy, "last_note", None),
            )
        )

    _write_equity(equity, valuation, ledger, growth, cursor, len(trading_days))
    ledger.accrue(growth[-1] / growth[cursor])  # Interest through the last day, for the total.
    if on_step is not None:
        on_step(len(dates), len(dates))
    if not events:
        warnings.append("No rebalance was executed; the portfolio stayed in cash.")
    cash_decisions = [event for event in events if not event.weights and event.note]
    if cash_decisions:
        first = cash_decisions[0]
        warnings.append(
            f"{len(cash_decisions)} of {len(events)} rebalance(s) moved to cash, "
            f"first on {first.decision_date.date()}: {first.note}"
        )
    return BacktestResult(
        strategy=strategy.name,
        equity=equity,
        rebalances=events,
        warnings=warnings,
        cash_interest=float(ledger.interest),  # Plain float: NumPy scalars are not JSON-serializable.
    )


def eligible_assets(window: pd.DataFrame) -> list[str]:
    """Assets with a price on the window's first and last day and few gaps in between."""
    if window.empty:
        return []
    first, last = window.iloc[0], window.iloc[-1]
    gaps = window.isna().mean()
    mask = first.notna() & last.notna() & (gaps <= MAX_MISSING_FRACTION)
    return list(window.columns[mask])


def cash_growth_factors(trading_days: pd.DatetimeIndex, annual_rate: float) -> np.ndarray:
    """Cumulative growth of one unit of cash from the first trading day to each trading day.

    Compounded over calendar days (weekends and holidays earn interest too),
    with the same 365.25-day year the CAGR metric uses.
    """
    days = (trading_days - trading_days[0]).days.to_numpy()
    return (1.0 + annual_rate) ** (days / 365.25)


def _write_equity(
    equity: pd.Series, valuation: pd.DataFrame, ledger: "_Ledger", growth: np.ndarray, start: int, stop: int
) -> None:
    """Mark the current holdings to market for trading-day positions [start, stop).

    `ledger.cash` is the balance on day `start`; it accrues interest across the segment.
    """
    if stop <= start:
        return
    holdings = valuation.iloc[start:stop].to_numpy() @ ledger.shares.to_numpy()
    cash = ledger.cash * growth[start:stop] / growth[start]
    equity.iloc[start:stop] = holdings + cash


class _Ledger:
    """Cash and share holdings, with the trade arithmetic."""

    def __init__(self, cash: float, shares: pd.Series) -> None:
        self.cash = cash
        self.shares = shares
        self.interest = 0.0  # Total interest the cash balance has earned.

    def accrue(self, factor: float) -> None:
        """Grow the cash balance by `factor` (interest since it was last brought up to date)."""
        earned = self.cash * (factor - 1.0)
        self.cash += earned
        self.interest += earned

    def value(self, prices_now: pd.Series) -> float:
        """Mark holdings to market."""
        return self.cash + float(self.shares @ prices_now)

    def rebalance(
        self,
        weights: Weights,
        *,
        prices_now: pd.Series,
        fee_rate: float,
        decision_date: pd.Timestamp,
        execution_date: pd.Timestamp,
        eligible_count: int,
        expected_performance: tuple[float, float, float] | None,
        note: str | None = None,
    ) -> RebalanceEvent:
        """Trade from the current holdings to `weights`, paying fees out of the portfolio."""
        value = self.value(prices_now)
        current_values = self.shares * prices_now
        target = pd.Series(weights, dtype=float).reindex(self.shares.index, fill_value=0.0)

        def traded_value(investment: float) -> float:
            return float((target * investment - current_values).abs().sum())

        investment = _solve_investment(value, fee_rate, traded_value)
        cost = fee_rate * traded_value(investment)
        new_values = target * investment
        self.shares = (new_values / prices_now.replace(0.0, float("nan"))).fillna(0.0)
        self.cash = value - float(new_values.sum()) - cost
        held = self.shares[self.shares > 0]
        return RebalanceEvent(
            decision_date=decision_date,
            execution_date=execution_date,
            portfolio_value=value,
            weights={asset: float(weight) for asset, weight in weights.items() if weight > 0},
            shares=held.to_dict(),
            cash=self.cash,
            turnover=traded_value(investment) / value if value > 0 else 0.0,
            transaction_cost=cost,
            eligible_assets=eligible_count,
            expected_performance=expected_performance,
            note=note,
        )


def _solve_investment(value: float, fee_rate: float, traded_value) -> float:
    """Largest investment I such that I + fee_rate * traded_value(I) <= value.

    The left-hand side is increasing in I (its slope is at least 1 - fee_rate),
    so a bisection converges; 60 iterations give sub-cent precision.
    """
    if fee_rate == 0:
        return value
    low, high = 0.0, value
    for _ in range(60):
        mid = (low + high) / 2
        if mid + fee_rate * traded_value(mid) <= value:
            low = mid
        else:
            high = mid
    return low
