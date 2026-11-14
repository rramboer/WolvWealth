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
* The equity curve is marked to market every trading day from `start` to
  `end`, including the days after the final rebalance.
"""

import pandas as pd

from wolvwealth.backtest.config import BacktestConfig
from wolvwealth.backtest.results import BacktestResult, RebalanceEvent
from wolvwealth.backtest.schedule import rebalance_dates
from wolvwealth.backtest.strategies import Strategy, StrategyError, Weights

MIN_WINDOW_ROWS = 20  # Fewer observations than this and the window is "insufficient history".
MAX_MISSING_FRACTION = 0.1  # Assets missing more than this share of the window's prices are skipped.


def run_backtest(prices: pd.DataFrame, config: BacktestConfig, strategy: Strategy) -> BacktestResult:
    """Simulate `strategy` on `prices` according to `config`."""
    missing = [ticker for ticker in config.universe if ticker not in prices.columns]
    if missing:
        raise ValueError(f"No price data available for: {', '.join(sorted(missing))}.")
    universe = list(config.universe)
    prices = prices.sort_index()
    start, end = pd.Timestamp(config.start), pd.Timestamp(config.end)
    trading_days = prices.index[(prices.index >= start) & (prices.index <= end)]
    if len(trading_days) < 2:
        raise ValueError(f"Fewer than two trading days of price data between {config.start} and {config.end}.")

    raw = prices[universe]
    filled = raw.ffill()  # Valuation prices: carry the last known price across gaps.
    valuation = filled.loc[trading_days].fillna(0.0)  # Unheld assets without a price contribute nothing.
    fee_rate = config.transaction_cost_bps / 10_000

    ledger = _Ledger(cash=config.initial_cash, shares=pd.Series(0.0, index=universe))
    equity = pd.Series(index=trading_days, dtype=float)
    events: list[RebalanceEvent] = []
    warnings: list[str] = []
    cursor = 0  # First trading-day position whose equity has not been written yet.

    for decision in rebalance_dates(trading_days, config.frequency):
        execution_position = trading_days.get_loc(decision) + config.execution_lag
        if execution_position >= len(trading_days):
            break
        window = raw.loc[decision - config.lookback : decision]
        eligible = eligible_assets(window)
        if len(window) < MIN_WINDOW_ROWS or not eligible:
            warnings.append(f"{decision.date()}: insufficient price history; holding previous positions.")
            continue
        try:
            weights = strategy.target_weights(window[eligible].ffill())
        except StrategyError as err:
            warnings.append(f"{decision.date()}: {err}; holding previous positions.")
            continue
        if weights is None:
            continue

        execution_date = trading_days[execution_position]
        prices_now = valuation.iloc[execution_position]
        untradable = [asset for asset in weights if prices_now.get(asset, 0.0) <= 0]
        if untradable:
            warnings.append(f"{decision.date()}: no execution price for {', '.join(untradable)}; holding.")
            continue
        _write_equity(equity, valuation, ledger, cursor, execution_position)
        cursor = execution_position
        events.append(
            ledger.rebalance(
                weights,
                prices_now=prices_now,
                fee_rate=fee_rate,
                decision_date=decision,
                execution_date=execution_date,
                eligible_count=len(eligible),
                expected_performance=getattr(strategy, "last_performance", None),
            )
        )

    _write_equity(equity, valuation, ledger, cursor, len(trading_days))
    if not events:
        warnings.append("No rebalance was executed; the portfolio stayed in cash.")
    return BacktestResult(strategy=strategy.name, equity=equity, rebalances=events, warnings=warnings)


def eligible_assets(window: pd.DataFrame) -> list[str]:
    """Assets with a price on the window's first and last day and few gaps in between."""
    if window.empty:
        return []
    first, last = window.iloc[0], window.iloc[-1]
    gaps = window.isna().mean()
    mask = first.notna() & last.notna() & (gaps <= MAX_MISSING_FRACTION)
    return list(window.columns[mask])


def _write_equity(equity: pd.Series, valuation: pd.DataFrame, ledger: "_Ledger", start: int, stop: int) -> None:
    """Mark the current holdings to market for trading-day positions [start, stop)."""
    if stop <= start:
        return
    values = valuation.iloc[start:stop].to_numpy() @ ledger.shares.to_numpy() + ledger.cash
    equity.iloc[start:stop] = values


class _Ledger:
    """Cash and share holdings, with the trade arithmetic."""

    def __init__(self, cash: float, shares: pd.Series) -> None:
        self.cash = cash
        self.shares = shares

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
