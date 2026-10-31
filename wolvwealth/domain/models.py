"""Entities: workspaces and portfolios, plus their naming rule."""

import dataclasses
import re

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,63}$")
NAME_RULE = "1-64 letters, digits, spaces, dots, dashes, or underscores"


def valid_name(name) -> bool:
    """Return true if name is usable as a workspace or portfolio name."""
    return isinstance(name, str) and NAME_RE.match(name) is not None


@dataclasses.dataclass
class Workspace:
    """A person using this install. No password; separation is organizational."""

    name: str
    created: str = ""
    portfolio_count: int = 0


@dataclasses.dataclass
class Portfolio:
    """A cash balance plus share counts per ticker, owned by one workspace."""

    id: int
    workspace: str
    name: str
    cash: float = 0.0
    holdings: dict = dataclasses.field(default_factory=dict)  # ticker -> shares
    #: Annual yield this portfolio's uninvested money earns (savings account,
    #: T-bills, a bond ETF...), as a fraction. None means the owner has not
    #: said, and the optimizer assumes 0% and says so in its output.
    risk_free_rate: float = None
    created: str = ""
