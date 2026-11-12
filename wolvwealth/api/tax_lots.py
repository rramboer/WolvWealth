"""Tax lots: shares of one ticker bought on one date at one price."""
import datetime
from dataclasses import dataclass


@dataclass(frozen=True)
class Lot:
    """One tax lot. order is the lot's position in the request, used to break ties."""

    shares: float
    cost_per_share: float
    acquired: datetime.date
    order: int = 0
