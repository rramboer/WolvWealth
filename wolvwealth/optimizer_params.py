"""Validation of optimizer inputs shared by the JSON API and the backtester.

Pure functions: no Flask, no global state. Callers translate
InvalidOptimizationInput into whatever error type suits their transport.
"""

import dataclasses
import math
import re

TOP_N_PATTERN = re.compile(r"top(\d+)", re.IGNORECASE)


class InvalidOptimizationInput(ValueError):
    """Raised when a universe or constraint value is malformed or infeasible."""


# Sanity ceilings. Anything beyond these is a typo or an attack, not a portfolio.
MAX_CASH = 1e12
MAX_SHARES = 1e12


def require_number(
    value: object,
    name: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
    exclusive_minimum: bool = False,
) -> float:
    """Return `value` as a finite float within [minimum, maximum], else raise.

    Rejects bools (a JSON `true` is not a number), NaN and +/-Infinity (which
    Python's JSON parser accepts and which slip through every `<`/`>` check),
    and out-of-range values.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidOptimizationInput(f"Invalid {name}. {name} must be a number.")
    number = float(value)
    if not math.isfinite(number):
        raise InvalidOptimizationInput(f"Invalid {name}. {name} must be a finite number.")
    if minimum is not None and (number < minimum or (exclusive_minimum and number == minimum)):
        bound = f"greater than {minimum:g}" if exclusive_minimum else f"at least {minimum:g}"
        raise InvalidOptimizationInput(f"Invalid {name}. {name} must be {bound}.")
    if maximum is not None and number > maximum:
        raise InvalidOptimizationInput(f"Invalid {name}. {name} must be at most {maximum:g}.")
    return number


def require_integer(value: object, name: str, *, minimum: int | None = None, maximum: int | None = None) -> int:
    """Return `value` as an int within [minimum, maximum], else raise. Floats such as 3.0 are rejected."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidOptimizationInput(f"Invalid {name}. {name} must be an integer.")
    if minimum is not None and value < minimum:
        raise InvalidOptimizationInput(f"Invalid {name}. {name} must be at least {minimum}.")
    if maximum is not None and value > maximum:
        raise InvalidOptimizationInput(f"Invalid {name}. {name} must be at most {maximum}.")
    return value


@dataclasses.dataclass(frozen=True)
class Constraints:
    """Portfolio constraints accepted by compute_max_sharpe_portfolio."""

    max_weight: float = 1.0  # No weights higher than this (1.0 disables).
    min_universal_weight: float = 0.0  # Every asset must have at least this weight.
    max_positions: int = -1  # Keep only the N largest positions (-1 disables).
    weight_threshold: float = 0.0005  # Drop weights below this cutoff.

    def __post_init__(self) -> None:
        """Reject malformed values regardless of how the object was built (API, CLI, code)."""
        require_number(self.max_weight, "max_weight", minimum=0.0, maximum=1.0, exclusive_minimum=True)
        require_number(self.min_universal_weight, "min_universal_weight", minimum=0.0, maximum=1.0)
        require_number(self.weight_threshold, "weight_threshold", minimum=0.0, maximum=1.0)
        if self.max_positions != -1:
            require_integer(self.max_positions, "max_positions", minimum=1)

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
    unknown = set(raw) - {"max_weight", "max_positions", "min_universal_weight"}
    if unknown:
        raise InvalidOptimizationInput(f"Unknown constraint(s): {', '.join(sorted(unknown))}.")
    values: dict = {}
    if "max_weight" in raw:
        max_weight = require_number(raw["max_weight"], "max_weight", minimum=0.0, maximum=1.0, exclusive_minimum=True)
        values["max_weight"] = round(max_weight, 2)
        if values["max_weight"] == 0:
            raise InvalidOptimizationInput("Invalid max_weight. max_weight must be greater than 0.")
    if "max_positions" in raw:
        # 0 means "no limit" for backwards compatibility with the original API.
        max_positions = require_integer(raw["max_positions"], "max_positions", minimum=0)
        values["max_positions"] = min(max_positions, universe_size) if max_positions > 0 else -1
    if "min_universal_weight" in raw:
        values["min_universal_weight"] = round(
            require_number(raw["min_universal_weight"], "min_universal_weight", minimum=0.0, maximum=1.0), 2
        )
    constraints = Constraints(**values)
    check_feasibility(constraints, universe_size)
    return constraints


FEASIBILITY_TOLERANCE = 1e-9


def feasibility_problem(constraints: Constraints, universe_size: int) -> str | None:
    """Why these constraints can never fully invest `universe_size` assets, or None if they can.

    A portfolio holds at most `positions` assets (the universe, or fewer when
    max_positions caps it), each at most max_weight, so it can invest at most
    positions * max_weight of the cash. Below 100% the solver has no solution.
    """
    if universe_size < 1:
        return "Infeasible. The universe has no assets."
    capped = constraints.max_positions != -1 and constraints.max_positions < universe_size
    positions = constraints.max_positions if capped else universe_size
    if positions * constraints.max_weight < 1 - FEASIBILITY_TOLERANCE:
        reachable = positions * constraints.max_weight
        source = f"max_positions ({positions})" if capped else f"{positions} stock(s) in the universe"
        return (
            f"Infeasible. {source} * max_weight ({constraints.max_weight:g}) can invest at most {reachable:.0%}; "
            f"full allocation needs 100%. Raise max_weight to at least {1 / positions:.2f} or add more stocks."
        )
    if constraints.min_universal_weight > constraints.max_weight:
        return "Invalid constraints. min_weight must be less than max_weight."
    if constraints.min_universal_weight * universe_size > 1 + FEASIBILITY_TOLERANCE:
        return (
            "Infeasible. min_universal_weight * universe size cannot exceed 1. "
            "Lower min_universal_weight or shrink the universe."
        )
    return None


def check_feasibility(constraints: Constraints, universe_size: int) -> None:
    """Raise InvalidOptimizationInput if the constraints can never fully invest the universe."""
    problem = feasibility_problem(constraints, universe_size)
    if problem:
        raise InvalidOptimizationInput(problem)
