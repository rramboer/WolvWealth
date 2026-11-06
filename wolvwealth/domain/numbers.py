"""What counts as a usable number, decided once for every way in.

JSON (``NaN``, ``Infinity``), ``float()`` ("nan", "inf", "1e400") and SQLite
all happily produce values that pass ``isinstance(value, float)`` but poison
arithmetic, fail NOT NULL constraints, or serialize as invalid JSON. Every
parser asks ``is_number`` instead of checking types itself.
"""

import math

#: Largest cash balance or share count accepted: above the largest personal
#: fortune on record, and small enough that float arithmetic stays exact to the cent.
MAX_AMOUNT = 2e12


def is_number(value) -> bool:
    """Return true for a finite int or float (bool excluded, NaN and infinities excluded)."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:  # an int too large to convert to a float
        return False


def is_amount(value) -> bool:
    """Return true for a number from 0 to MAX_AMOUNT: a usable cash balance or share count."""
    return is_number(value) and 0 <= value <= MAX_AMOUNT


#: Range a 13-week Treasury bill yield can believably have, as a fraction.
#: It has been slightly negative and, at its 1981 peak, about 16%.
TBILL_RATE_RANGE = (-0.01, 0.25)


def is_plausible_tbill_rate(value) -> bool:
    """Return true if ``value`` could be a real 13-week T-bill yield (a fraction)."""
    low, high = TBILL_RATE_RANGE
    return is_number(value) and low <= value <= high
