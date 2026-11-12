"""Tax lots: shares of one ticker bought on one date at one price.

These functions choose which lots a sell comes out of. They estimate
federal capital gains only; they know nothing about the investor's other
accounts, loss carryovers or state taxes, and aren't tax advice.
"""
import datetime
from dataclasses import dataclass

METHODS = ("min_tax", "fifo", "hifo", "lifo")
DEFAULT_METHOD = "min_tax"
# Used only to rank lots for min_tax: a mid-bracket ordinary rate for short
# term gains and the common 15% rate for long term gains.
DEFAULT_TAX_RATES = {"short_term": 0.24, "long_term": 0.15}

# A loss sale within this many days of a purchase of the same stock may be a wash sale.
WASH_SALE_DAYS = 30

# Share amounts closer than this are treated as equal (float rounding).
EPSILON = 1e-6


@dataclass(frozen=True)
class Lot:
    """One tax lot. order is the lot's position in the request, used to break ties."""

    shares: float
    cost_per_share: float
    acquired: datetime.date
    order: int = 0


@dataclass(frozen=True)
class LotSale:
    """Shares sold out of one lot."""

    lot: Lot
    shares: float
    price: float
    term: str  # "short" or "long"

    @property
    def gain(self) -> float:
        """Return the realized gain, negative for a loss, before rounding."""
        return (self.price - self.lot.cost_per_share) * self.shares


def one_year_after(day: datetime.date) -> datetime.date:
    """Return the same calendar date a year later; February 29 becomes February 28."""
    try:
        return day.replace(year=day.year + 1)
    except ValueError:
        return day.replace(year=day.year + 1, day=28)


def holding_term(acquired: datetime.date, sale_date: datetime.date) -> str:
    """Return "long" if held more than one year at sale_date, else "short".

    The holding period starts the day after purchase and includes the sale
    date (IRS Publication 550), so a lot bought 2025-02-05 is short term if
    sold 2026-02-05 and long term from 2026-02-06.
    """
    return "long" if sale_date > one_year_after(acquired) else "short"


def sort_key(method: str, price: float, sale_date: datetime.date, tax_rates: dict):
    """Return the sort key that puts lots in the order method sells them."""
    if method == "fifo":  # Oldest first.
        return lambda lot: (lot.acquired, lot.order)
    if method == "lifo":  # Newest first.
        return lambda lot: (-lot.acquired.toordinal(), lot.order)
    if method == "hifo":  # Highest cost first.
        return lambda lot: (-lot.cost_per_share, lot.acquired, lot.order)
    if method == "min_tax":
        # Least tax per share first; losses are negative tax, so they come first.
        # With flat rates this gives the lowest total tax for the shares sold.
        def tax_per_share(lot):
            rate = tax_rates[holding_term(lot.acquired, sale_date) + "_term"]
            return ((price - lot.cost_per_share) * rate, lot.acquired, lot.order)

        return tax_per_share
    raise ValueError(f"Unknown lot method: {method}")


def select_lots(
    lots: list,
    shares_to_sell: float,
    price: float,
    sale_date: datetime.date,
    method: str = DEFAULT_METHOD,
    tax_rates: dict = None,
) -> list:
    """Return the LotSales that make up a sale of shares_to_sell shares at price.

    Lots are used whole in the order method gives; the last one used may be
    split. Raises ValueError if the lots hold fewer shares than shares_to_sell.
    """
    tax_rates = DEFAULT_TAX_RATES if tax_rates is None else tax_rates
    remaining = shares_to_sell
    sales = []
    for lot in sorted(lots, key=sort_key(method, price, sale_date, tax_rates)):
        if remaining <= EPSILON:
            break
        # Take the whole lot if what's left is the lot (within float rounding).
        shares = lot.shares if lot.shares <= remaining + EPSILON else remaining
        sales.append(LotSale(lot, shares, price, holding_term(lot.acquired, sale_date)))
        remaining -= shares
    if remaining > EPSILON:
        raise ValueError(f"Lots hold {shares_to_sell - remaining} shares, fewer than the {shares_to_sell} to sell.")
    return sales


def wash_sale_risk(lots: list, sales: list, sale_date: datetime.date):
    """Return (loss, replacement_dates) for sales that may be wash sales.

    A sale at a loss may be a wash sale if the same stock was bought within
    30 days before it; here, another of the ticker's lots acquired in that
    window. loss is the total of those losses (negative); replacement_dates
    lists the dates of the recent lots. Purchases after the sale, or in other
    accounts, can't be seen here.
    """
    window_start = sale_date - datetime.timedelta(days=WASH_SALE_DAYS)
    loss = 0.0
    replacement_dates = set()
    for sale in sales:
        if sale.gain >= 0:
            continue
        recent = [
            lot.acquired
            for lot in lots
            if lot.order != sale.lot.order and window_start <= lot.acquired <= sale_date
        ]
        if recent:
            loss += sale.gain
            replacement_dates.update(recent)
    return loss, sorted(replacement_dates)
