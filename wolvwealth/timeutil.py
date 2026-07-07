"""Shared timestamp helpers.

The database stores naive UTC timestamps ("YYYY-MM-DD HH:MM:SS"). These
helpers convert to and from US Eastern time (DST-aware) for display and input.
"""

import datetime
from zoneinfo import ZoneInfo

UTC = ZoneInfo("UTC")
EASTERN = ZoneInfo("America/New_York")

STORAGE_FORMAT = "%Y-%m-%d %H:%M:%S"
DISPLAY_FORMAT = "%Y-%m-%d %I:%M %p"


def utc_to_eastern_display(timestamp: str) -> str:
    """Convert a stored UTC timestamp to a human-readable US Eastern string."""
    moment = datetime.datetime.strptime(timestamp, STORAGE_FORMAT).replace(tzinfo=UTC)
    return moment.astimezone(EASTERN).strftime(DISPLAY_FORMAT) + " ET"


def eastern_to_utc_storage(timestamp: str) -> str:
    """Convert a US Eastern "YYYY-MM-DD HH:MM:SS" string to a stored UTC timestamp."""
    moment = datetime.datetime.strptime(timestamp, STORAGE_FORMAT).replace(tzinfo=EASTERN)
    return moment.astimezone(UTC).strftime(STORAGE_FORMAT)
