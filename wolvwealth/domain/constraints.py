"""Feasibility of a constraint set, checked before the solver ever runs.

Every rule about how two constraints interact lives here, in ``RULES``. This
is the one place to extend when a new constraint is added, and the reason it
is a registry rather than a run of ``if`` statements inside the parser: a
constraint that is individually valid can still be impossible in combination
(10% per holding across 8 holdings only ever invests 80% of the money), and
the solver's own answer to that is the word "infeasible" with no indication
of which input to change.

Two properties the rest of the app depends on:

* **Every conflict is reported, not just the first.** Someone who set three
  contradictory numbers should learn that in one round trip rather than
  three, which matters most for an agent editing a JSON body and for a
  browser form the user has to retype.
* **Every conflict names a concrete remedy.** ``Conflict.remedy`` says which
  field to change and to what value, so the message is actionable without
  the reader working the arithmetic back out.

Conflicts travel to callers as a ``ValidationError`` whose ``details``
carries them structurally (the JSON API publishes that as ``error.payload``),
so an agent can branch on the fields involved instead of parsing prose.
"""

import dataclasses
import math

from wolvwealth.domain.errors import ValidationError

#: Slack when comparing constraint products against 1. Weights are rounded to
#: two decimals on the way in, so an exactly-saturated set (ten holdings at
#: 10%) must read as feasible rather than failing on float representation.
TOLERANCE = 1e-9


@dataclasses.dataclass(frozen=True)
class Conflict:
    """One reason a constraint set cannot be satisfied, and how to fix it.

    ``fields`` are the request fields involved, so a caller can highlight
    exactly the inputs at fault; ``reason`` states the contradiction with its
    numbers; ``remedy`` names a change that resolves it.
    """

    fields: tuple
    reason: str
    remedy: str

    def as_dict(self) -> dict:
        """The JSON-serializable form published in an error payload."""
        return {"fields": list(self.fields), "reason": self.reason, "remedy": self.remedy}


def _position_cap(request) -> int:
    """How many holdings the request can end up with at most.

    ``max_positions`` when it is set, otherwise the universe itself: a
    four-ticker universe caps the portfolio at four holdings just as firmly
    as asking for four would, and the same arithmetic makes both infeasible
    against a small ``max_weight``.
    """
    universe_size = len(request.universe)
    if request.max_positions is None:
        return universe_size
    return min(request.max_positions, universe_size)


def _max_weight_cannot_fill_the_portfolio(request):
    """max_weight x the number of holdings allowed must reach 100%."""
    cap, max_weight = _position_cap(request), request.max_weight
    reachable = cap * max_weight
    if reachable >= 1 - TOLERANCE:
        return None
    # max_weight is stored to two decimals, so the suggestion must round up to
    # two decimals as well or following it would land back here.
    enough_weight = math.ceil(100 / cap) / 100
    enough_positions = math.ceil(1 / max_weight - TOLERANCE)
    if request.max_positions is None or request.max_positions > len(request.universe):
        # The universe, not max_positions, is what limits the holding count.
        return Conflict(
            fields=("constraints.max_weight", "universe"),
            reason=(
                f"max_weight {max_weight} caps each of the {cap} tickers in the universe at "
                f"{max_weight:.0%}, which allocates at most {reachable:.0%} of the portfolio."
            ),
            remedy=(
                f"Raise max_weight to at least {enough_weight}, or widen the universe to at least "
                f"{enough_positions} tickers."
            ),
        )
    return Conflict(
        fields=("constraints.max_positions", "constraints.max_weight"),
        reason=(
            f"max_positions {cap} holdings at max_weight {max_weight} each allocates at most "
            f"{reachable:.0%} of the portfolio, leaving {1 - reachable:.0%} with nowhere to go."
        ),
        remedy=(f"Raise max_positions to at least {enough_positions}, or max_weight to at least {enough_weight}."),
    )


def _min_weight_overfills_the_portfolio(request):
    """min_universal_weight applies to every universe ticker, so n x min must fit in 100%."""
    minimum, universe_size = request.min_universal_weight, len(request.universe)
    required = minimum * universe_size
    if minimum <= 0 or required <= 1 + TOLERANCE:
        return None
    # Round down: a suggestion that rounds up would overfill by the same rule.
    small_enough = math.floor(100 / universe_size) / 100
    fits = math.floor(1 / minimum + TOLERANCE)
    return Conflict(
        fields=("constraints.min_universal_weight", "universe"),
        reason=(
            f"min_universal_weight {minimum} is a floor under every one of the {universe_size} tickers in "
            f"the universe, which requires {required:.0%} of the portfolio."
        ),
        remedy=(
            f"Lower min_universal_weight to at most {small_enough}, or narrow the universe to at most "
            f"{fits} tickers."
        ),
    )


def _min_weight_forces_every_ticker_to_be_held(request):
    """A floor over the whole universe is incompatible with capping the holding count.

    ``min_universal_weight`` is a bound on every asset in the universe, not
    on the ones that happen to be chosen, so a positive floor means every
    ticker is held and the position count is the universe size by
    construction. Asking for fewer is a contradiction the solver reports only
    as "infeasible".
    """
    minimum, universe_size = request.min_universal_weight, len(request.universe)
    if minimum <= 0 or request.max_positions is None or request.max_positions >= universe_size:
        return None
    return Conflict(
        fields=("constraints.min_universal_weight", "constraints.max_positions"),
        reason=(
            f"min_universal_weight {minimum} requires all {universe_size} universe tickers to be held, "
            f"but max_positions {request.max_positions} allows only {request.max_positions}."
        ),
        remedy=(
            f"Set min_universal_weight to 0, or raise max_positions to {universe_size}, or narrow the "
            f"universe to {request.max_positions} tickers."
        ),
    )


def _min_weight_above_max_weight(request):
    """No single weight can sit under the floor and over the cap at once."""
    minimum, maximum = request.min_universal_weight, request.max_weight
    if minimum <= maximum:
        return None
    return Conflict(
        fields=("constraints.min_universal_weight", "constraints.max_weight"),
        reason=f"min_universal_weight {minimum} is above max_weight {maximum}, so no weight satisfies both.",
        remedy=f"Set min_universal_weight less than max_weight {maximum}, or raise max_weight above {minimum}.",
    )


#: Checked in order; every rule that fires is reported. A rule takes the
#: parsed request and returns a Conflict or None. Add new interactions here.
RULES = (
    _max_weight_cannot_fill_the_portfolio,
    _min_weight_overfills_the_portfolio,
    _min_weight_forces_every_ticker_to_be_held,
    _min_weight_above_max_weight,
)


def conflicts(request) -> list:
    """Every way this request's constraints contradict each other, in rule order."""
    return [conflict for conflict in (rule(request) for rule in RULES) if conflict is not None]


def describe(found: list) -> str:
    """One message covering ``found``, numbered when there is more than one."""
    if len(found) == 1:
        return f"Infeasible. {found[0].reason} {found[0].remedy}"
    joined = " ".join(f"({i}) {c.reason} {c.remedy}" for i, c in enumerate(found, start=1))
    return f"Infeasible. {len(found)} constraint conflicts. {joined}"


def check(request) -> None:
    """Raise ValidationError describing every conflict, or return if there are none."""
    found = conflicts(request)
    if not found:
        return
    raise ValidationError(describe(found), details={"conflicts": [conflict.as_dict() for conflict in found]})
