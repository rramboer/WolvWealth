"""The risk-free rate a portfolio's idle money earns: validation and the T-bill shortcut.

Shared by the optimize and holdings-import use cases, so the API accepts
the same values everywhere a rate can be given: a decimal fraction, or the
string ``"tbill"`` meaning the latest cached 13-week Treasury yield.
"""

from wolvwealth.domain.errors import ValidationError
from wolvwealth.domain.numbers import is_number

TBILL = "tbill"


def resolve(value, tbill: dict, field: str = "risk_free_rate") -> tuple:
    """Return ``(rate, source)`` for a user-supplied rate, or raise ValidationError.

    ``tbill`` is the cached ``{"rate", "as_of"}`` reference, or None when it
    has never been fetched. ``value`` may be None, which returns ``(None,
    None)`` so callers can treat "not given" uniformly.
    """
    if value is None:
        return None, None
    if value == TBILL:
        if tbill is None:
            raise ValidationError(
                f"Invalid {field}. The T-bill rate has not been fetched yet; run `wolvwealth refresh-data` "
                "(or wait for the background refresh) and try again, or pass a number."
            )
        return tbill["rate"], TBILL
    if not is_number(value):
        raise ValidationError(f'Invalid {field}. {field} must be a number, or "tbill" for the current T-bill yield.')
    if not -1 < value < 1:
        raise ValidationError(f"Invalid {field}. {field} is a decimal fraction: 0.04 means 4%.")
    return round(value, 6), "request"
