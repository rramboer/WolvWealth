"""Validation of optimizer inputs shared by the JSON API and the backtester.

Pure functions: no Flask, no global state. Callers translate
InvalidOptimizationInput into whatever error type suits their transport.
"""

import dataclasses
import re

TOP_N_PATTERN = re.compile(r"top(\d+)", re.IGNORECASE)


class InvalidOptimizationInput(ValueError):
    """Raised when a universe or constraint value is malformed or infeasible."""


@dataclasses.dataclass(frozen=True)
class Constraints:
    """Portfolio constraints accepted by compute_max_sharpe_portfolio."""

    max_weight: float = 1.0  # No weights higher than this (1.0 disables).
    min_universal_weight: float = 0.0  # Every asset must have at least this weight.
    max_positions: int = -1  # Keep only the N largest positions (-1 disables).
    weight_threshold: float = 0.0005  # Drop weights below this cutoff.

    def as_kwargs(self) -> dict:
        """Keyword arguments for compute_max_sharpe_portfolio."""
        return dataclasses.asdict(self)


def resolve_universe(entries: list, ticker_universe: list[str]) -> list[str]:
    """Expand a list of tickers / "topN" aliases into a de-duplicated ticker list.

    Tickers are case-insensitive and must be present in ticker_universe, which is
    ordered by market cap so that "top50" means its first 50 entries.
    """
    if not isinstance(entries, list):
        raise InvalidOptimizationInput("Invalid universe. universe must be a list.")
    universe: list[str] = []
    for entry in entries:
        if not isinstance(entry, str):
            raise InvalidOptimizationInput("Invalid universe. universe must be list of strings.")
        ticker = entry.upper()
        if ticker in ticker_universe:
            universe.append(ticker)
            continue
        top_n = TOP_N_PATTERN.fullmatch(entry)
        if top_n:  # e.g. "top50" expands to the 50 largest stocks by market cap
            universe.extend(ticker_universe[: int(top_n.group(1))])
            continue
        raise InvalidOptimizationInput(f"Invalid symbol in universe: {entry}.")
    return list(dict.fromkeys(universe))


def parse_constraints(raw: dict, universe_size: int) -> Constraints:
    """Validate a constraints mapping (as sent to the API) and check feasibility."""
    if not isinstance(raw, dict):
        raise InvalidOptimizationInput("Invalid constraints. constraints must be a dictionary.")
    constraints = Constraints()
    if "max_weight" in raw:
        value = raw["max_weight"]
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise InvalidOptimizationInput("Invalid max_weight. max_weight must be a number.")
        if value < 0.0 or value > 1.0:
            raise InvalidOptimizationInput("Invalid max_weight. max_weight must be greater than 0 and less than 1.")
        constraints = dataclasses.replace(constraints, max_weight=round(value, 2))
    if "max_positions" in raw:
        value = raw["max_positions"]
        if not isinstance(value, int) or isinstance(value, bool):
            raise InvalidOptimizationInput("Invalid max_positions. max_positions must be an integer.")
        if value < 0:
            raise InvalidOptimizationInput("Invalid max_positions. max_positions must be greater than 0.")
        constraints = dataclasses.replace(constraints, max_positions=min(value, universe_size))
    if "min_universal_weight" in raw:
        value = raw["min_universal_weight"]
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise InvalidOptimizationInput("Invalid min_universal_weight. min_universal_weight must be a number.")
        if value < 0.0 or value > 1.0:
            raise InvalidOptimizationInput(
                "Invalid min_universal_weight. min_universal_weight must be greater than 0 and less than 1."
            )
        constraints = dataclasses.replace(constraints, min_universal_weight=round(value, 2))
    check_feasibility(constraints, universe_size)
    return constraints


def check_feasibility(constraints: Constraints, universe_size: int) -> None:
    """Reject constraint combinations that can never produce a fully allocated portfolio."""
    if constraints.max_positions != -1 and constraints.max_positions * constraints.max_weight < 1:
        raise InvalidOptimizationInput(
            "Infeasible. max_positions * max_weight must be greater than or equal to 1 for full allocation."
        )
    if constraints.min_universal_weight > constraints.max_weight:
        raise InvalidOptimizationInput("Invalid constraints. min_weight must be less than max_weight.")
    if constraints.min_universal_weight * universe_size > 1:
        raise InvalidOptimizationInput(
            "Infeasible. min_universal_weight * universe size cannot exceed 1. "
            "Lower min_universal_weight or shrink the universe."
        )
