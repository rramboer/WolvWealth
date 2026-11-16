"""Turn an untrusted JSON payload into a validated BacktestConfig.

Transport-agnostic: the JSON API hands the decoded body here and converts
InvalidOptimizationInput into its own error response. Limits protect the
server from requests that would take minutes to run.
"""

import dataclasses
import datetime

import pandas as pd

from wolvwealth.backtest.config import BacktestConfig, Frequency
from wolvwealth.backtest.preflight import check_runnable
from wolvwealth.backtest.schedule import rebalance_dates
from wolvwealth.backtest.service import DEFAULT_BENCHMARK_TICKERS
from wolvwealth.optimizer_params import (
    Constraints,
    InvalidOptimizationInput,
    parse_constraints,
    require_integer,
    require_number,
    resolve_universe,
)


@dataclasses.dataclass(frozen=True)
class BacktestLimits:
    """Upper bounds on the work one request may ask for."""

    max_rebalances: int = 120
    max_universe_size: int = 100
    max_benchmarks: int = 3


DEFAULT_LIMITS = BacktestLimits()  # Frozen, so one shared instance is safe as a default.


@dataclasses.dataclass(frozen=True)
class BacktestRequest:
    """A validated request: what to run and how to shape the response."""

    config: BacktestConfig
    benchmark_tickers: tuple[str, ...]
    include_equity: bool


def parse_backtest_request(
    payload: object,
    *,
    ticker_universe: list[str],
    prices: pd.DataFrame,
    limits: BacktestLimits = DEFAULT_LIMITS,
) -> BacktestRequest:
    """Validate `payload` and enforce `limits`.

    Accepted keys (all optional unless noted): start (required, YYYY-MM-DD),
    end, initial_cash, frequency, universe, lookback_days, execution_lag,
    transaction_cost_bps, constraints, benchmarks, include_equity.
    """
    if not isinstance(payload, dict):
        raise InvalidOptimizationInput("Parse Error. Request body must be a JSON object.")
    unknown = set(payload) - _KNOWN_KEYS
    if unknown:
        raise InvalidOptimizationInput(f"Unknown field(s): {', '.join(sorted(unknown))}.")

    start = _date(payload, "start", required=True)
    end = _date(payload, "end") or prices.index[-1].date()
    if pd.Timestamp(start) > prices.index[-1]:
        raise InvalidOptimizationInput(f"start is after the last available price date ({prices.index[-1].date()}).")
    universe = _universe(payload, ticker_universe, limits)
    constraints = Constraints()
    if "constraints" in payload:
        constraints = parse_constraints(payload["constraints"], len(universe))

    try:
        config = BacktestConfig(
            start=start,
            end=end,
            universe=tuple(universe),
            initial_cash=_number(payload, "initial_cash", default=100_000.0),
            frequency=_frequency(payload),
            lookback_days=_integer(payload, "lookback_days", default=3 * 365),
            execution_lag=_integer(payload, "execution_lag", default=1),
            transaction_cost_bps=_number(payload, "transaction_cost_bps", default=0.0),
            constraints=constraints,
        )
    except InvalidOptimizationInput as err:
        raise InvalidOptimizationInput(str(err)) from err
    # Date range, rebalance limit, and "could anything actually trade?" — all before any charge.
    check_runnable(prices, config, max_rebalances=limits.max_rebalances)

    benchmarks = _benchmarks(payload, prices, limits)
    include_equity = payload.get("include_equity", True)
    if not isinstance(include_equity, bool):
        raise InvalidOptimizationInput("Invalid include_equity. include_equity must be a boolean.")
    return BacktestRequest(config=config, benchmark_tickers=benchmarks, include_equity=include_equity)


def count_rebalances(prices: pd.DataFrame, config: BacktestConfig) -> int:
    """Number of rebalance decisions the config implies on this price calendar."""
    index = prices.index
    trading_days = index[(index >= pd.Timestamp(config.start)) & (index <= pd.Timestamp(config.end))]
    return len(rebalance_dates(trading_days, config.frequency))


_KNOWN_KEYS = {
    "start",
    "end",
    "initial_cash",
    "frequency",
    "universe",
    "lookback_days",
    "execution_lag",
    "transaction_cost_bps",
    "constraints",
    "benchmarks",
    "include_equity",
}


def _date(payload: dict, key: str, required: bool = False) -> datetime.date | None:
    if key not in payload or payload[key] is None:
        if required:
            raise InvalidOptimizationInput(f"Missing required field: {key} (YYYY-MM-DD).")
        return None
    value = payload[key]
    if not isinstance(value, str):
        raise InvalidOptimizationInput(f"Invalid {key}. {key} must be a YYYY-MM-DD string.")
    try:
        return datetime.date.fromisoformat(value)
    except ValueError as err:
        raise InvalidOptimizationInput(f"Invalid {key}. {key} must be a YYYY-MM-DD string.") from err


def _number(payload: dict, key: str, default: float) -> float:
    """Type and finiteness only; BacktestConfig applies the ranges."""
    return require_number(payload.get(key, default), key)


def _integer(payload: dict, key: str, default: int) -> int:
    """Type only; BacktestConfig applies the ranges."""
    return require_integer(payload.get(key, default), key)


def _frequency(payload: dict) -> Frequency:
    value = payload.get("frequency", Frequency.MONTHLY.value)
    if not isinstance(value, str) or value.lower() not in Frequency.__members__.values():
        choices = ", ".join(f.value for f in Frequency)
        raise InvalidOptimizationInput(f"Invalid frequency. frequency must be one of: {choices}.")
    return Frequency(value.lower())


def _universe(payload: dict, ticker_universe: list[str], limits: BacktestLimits) -> list[str]:
    requested = payload.get("universe", ["top50"])
    if isinstance(requested, list) and not requested:
        requested = ["top50"]
    universe = resolve_universe(requested, ticker_universe)
    if len(universe) > limits.max_universe_size:
        raise InvalidOptimizationInput(
            f"Universe too large: {len(universe)} tickers, the limit is {limits.max_universe_size}."
        )
    return universe


def _benchmarks(payload: dict, prices: pd.DataFrame, limits: BacktestLimits) -> tuple[str, ...]:
    if "benchmarks" not in payload:
        return tuple(ticker for ticker in DEFAULT_BENCHMARK_TICKERS if ticker in prices.columns)
    requested = payload["benchmarks"]
    if not isinstance(requested, list) or not all(isinstance(entry, str) for entry in requested):
        raise InvalidOptimizationInput("Invalid benchmarks. benchmarks must be a list of ticker strings.")
    if len(requested) > limits.max_benchmarks:
        raise InvalidOptimizationInput(f"Too many benchmarks: the limit is {limits.max_benchmarks}.")
    tickers = list(dict.fromkeys(entry.upper() for entry in requested))
    missing = [ticker for ticker in tickers if ticker not in prices.columns]
    if missing:
        raise InvalidOptimizationInput(f"No price data for benchmark(s): {', '.join(missing)}.")
    return tuple(tickers)
