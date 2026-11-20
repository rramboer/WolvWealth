"""Validation of optimizer inputs shared by the JSON API and the backtester.

Pure functions: no Flask, no global state. Callers translate
InvalidOptimizationInput into whatever error type suits their transport.
"""

import dataclasses
import enum
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
    try:
        number = float(value)
    except OverflowError as err:  # A JSON integer with ~309+ digits is a valid int but no float can hold it.
        raise InvalidOptimizationInput(f"Invalid {name}. {name} is out of range.") from err
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


class ObjectiveName(enum.StrEnum):
    """What the optimizer maximizes or minimizes.

    All five are special cases of one mean-variance utility, expected return
    minus a risk penalty, the way enterprise optimizers frame it:
    max_utility is that function with an explicit risk aversion, and the
    others fix the trade-off by a ratio (max_sharpe), a risk budget
    (efficient_risk), a return target (efficient_return), or by ignoring
    return altogether (min_volatility).
    """

    MAX_SHARPE = "max_sharpe"  # Most expected return per unit of volatility.
    MIN_VOLATILITY = "min_volatility"  # Calmest fully-invested portfolio.
    EFFICIENT_RISK = "efficient_risk"  # Highest expected return with volatility <= target_volatility.
    EFFICIENT_RETURN = "efficient_return"  # Lowest volatility with expected return >= target_return.
    MAX_UTILITY = "max_utility"  # Highest return - risk_aversion / 2 * variance.


# Objective parameters: which objective needs which, and their sane ranges.
OBJECTIVE_PARAMETERS = {
    ObjectiveName.EFFICIENT_RISK: "target_volatility",
    ObjectiveName.EFFICIENT_RETURN: "target_return",
    ObjectiveName.MAX_UTILITY: "risk_aversion",
}
MAX_TARGET_VOLATILITY = 5.0  # 500% a year; no real portfolio is near this.
MIN_TARGET_RETURN = -1.0  # Losing everything; below that is meaningless.
MAX_TARGET_RETURN = 5.0  # 500% a year.
MAX_RISK_AVERSION = 1_000.0  # Far past the point where max_utility is just min_volatility.


@dataclasses.dataclass(frozen=True)
class Objective:
    """The optimization objective and its parameter, if it takes one.

    Exactly the parameter the objective needs must be set: efficient_risk
    takes target_volatility, efficient_return takes target_return,
    max_utility takes risk_aversion, and max_sharpe and min_volatility take
    none. All rates are annual decimals (0.15 means 15%).
    """

    name: ObjectiveName = ObjectiveName.MAX_SHARPE
    target_volatility: float | None = None
    target_return: float | None = None
    risk_aversion: float | None = None

    def __post_init__(self) -> None:
        """Reject malformed values regardless of how the object was built (API, CLI, code).

        A plain-string name is accepted and stored as an ObjectiveName, and
        parameters are stored as floats (pypfopt rejects an int target_return).
        """
        if isinstance(self.name, str) and self.name in ObjectiveName.__members__.values():
            object.__setattr__(self, "name", ObjectiveName(self.name))
        if not isinstance(self.name, ObjectiveName):
            raise InvalidOptimizationInput(f"Invalid objective. objective must be one of: {_objective_choices()}.")
        needed = OBJECTIVE_PARAMETERS.get(self.name)
        for parameter in OBJECTIVE_PARAMETERS.values():
            value = getattr(self, parameter)
            if parameter == needed and value is None:
                raise InvalidOptimizationInput(f"Invalid objective. {self.name} requires {parameter}.")
            if parameter != needed and value is not None:
                raise InvalidOptimizationInput(f"Invalid objective. {self.name} does not take {parameter}.")
        if self.target_volatility is not None:
            require_number(
                self.target_volatility,
                "target_volatility",
                minimum=0.0,
                maximum=MAX_TARGET_VOLATILITY,
                exclusive_minimum=True,
            )
        if self.target_return is not None:
            require_number(self.target_return, "target_return", minimum=MIN_TARGET_RETURN, maximum=MAX_TARGET_RETURN)
        if self.risk_aversion is not None:
            require_number(
                self.risk_aversion, "risk_aversion", minimum=0.0, maximum=MAX_RISK_AVERSION, exclusive_minimum=True
            )
        if needed is not None:
            object.__setattr__(self, needed, float(getattr(self, needed)))

    @property
    def parameter(self) -> float | None:
        """The objective's one parameter, or None for max_sharpe and min_volatility."""
        name = OBJECTIVE_PARAMETERS.get(self.name)
        return None if name is None else getattr(self, name)

    def to_dict(self) -> dict:
        """JSON-friendly representation: the name plus the parameter it uses."""
        data: dict = {"name": str(self.name)}
        if self.name in OBJECTIVE_PARAMETERS:
            data[OBJECTIVE_PARAMETERS[self.name]] = self.parameter
        return data


def _objective_choices() -> str:
    return ", ".join(name.value for name in ObjectiveName)


def parse_objective(raw: object) -> Objective:
    """Validate an objective as sent to the API.

    Accepts a bare name ("min_volatility") or an object with a name and the
    objective's parameter ({"name": "efficient_risk", "target_volatility": 0.15}).
    """
    if isinstance(raw, str):
        raw = {"name": raw}
    if not isinstance(raw, dict):
        raise InvalidOptimizationInput("Invalid objective. objective must be a name or an object with a name.")
    unknown = set(raw) - {"name", *OBJECTIVE_PARAMETERS.values()}
    if unknown:
        raise InvalidOptimizationInput(f"Unknown objective field(s): {', '.join(sorted(map(str, unknown)))}.")
    name = raw.get("name")
    if not isinstance(name, str) or name.lower() not in ObjectiveName.__members__.values():
        raise InvalidOptimizationInput(f"Invalid objective. objective must be one of: {_objective_choices()}.")
    values = {}
    for parameter in OBJECTIVE_PARAMETERS.values():
        if parameter in raw:
            values[parameter] = require_number(raw[parameter], parameter)  # Type here; Objective checks ranges.
    return Objective(ObjectiveName(name.lower()), **values)


@dataclasses.dataclass(frozen=True)
class Constraints:
    """Portfolio constraints accepted by compute_portfolio, applied with every objective."""

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
        """Keyword arguments for compute_portfolio."""
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
            universe.extend(ticker_universe[: _top_n_count(top_n.group(1), entry, len(ticker_universe))])
            continue
        raise InvalidOptimizationInput(f"Invalid symbol in universe: {entry}.")
    return list(dict.fromkeys(universe))


def _top_n_count(digits: str, entry: str, universe_size: int) -> int:
    """N from a "topN" alias, clamped to the universe size.

    Any N longer than the universe size's digit count is clamped without
    calling int(), which raises ValueError past Python's 4300-digit limit.
    """
    digits = digits.lstrip("0")
    if not digits:
        raise InvalidOptimizationInput(f"Invalid universe entry {entry}: top0 selects no stocks.")
    if len(digits) > len(str(universe_size)):
        return universe_size
    return min(int(digits), universe_size)


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
        hint = f"Raise max_weight to at least {minimum_feasible_max_weight(positions):.2f}"
        if constraints.max_weight > 0:
            hint += f", or hold at least {math.ceil(1 / constraints.max_weight - FEASIBILITY_TOLERANCE)} stocks"
        return (
            f"Infeasible. {source} * max_weight ({constraints.max_weight:g}) can invest at most {reachable:.0%}; "
            f"full allocation needs 100%. {hint}."
        )
    if constraints.min_universal_weight > constraints.max_weight:
        return "Invalid constraints. min_weight must be less than max_weight."
    if constraints.min_universal_weight * universe_size > 1 + FEASIBILITY_TOLERANCE:
        return (
            "Infeasible. min_universal_weight * universe size cannot exceed 1. "
            "Lower min_universal_weight or shrink the universe."
        )
    return None


def minimum_feasible_max_weight(positions: int) -> float:
    """Smallest max_weight, at the API's 2-decimal precision, with which `positions` assets reach 100%.

    Rounded up, not to nearest: 1/3 to nearest is 0.33, and 3 * 0.33 = 0.99
    is still infeasible, so the hint must say 0.34.
    """
    return math.ceil(100 / positions - FEASIBILITY_TOLERANCE) / 100


def check_feasibility(constraints: Constraints, universe_size: int) -> None:
    """Raise InvalidOptimizationInput if the constraints can never fully invest the universe."""
    problem = feasibility_problem(constraints, universe_size)
    if problem:
        raise InvalidOptimizationInput(problem)
