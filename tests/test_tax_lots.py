"""Tests for tax lot selection (wolvwealth/api/tax_lots.py)."""
from datetime import date

import pytest

from wolvwealth.api.tax_lots import Lot, holding_term, select_lots, wash_sale_risk

SALE = date(2026, 11, 12)
PRICE = 100.0

# Order in the request: an old gain, a recent loss, an old loss, a recent gain.
OLD_GAIN = Lot(shares=10, cost_per_share=20.0, acquired=date(2020, 1, 15), order=0)
NEW_LOSS = Lot(shares=5, cost_per_share=110.0, acquired=date(2026, 9, 1), order=1)
OLD_LOSS = Lot(shares=4, cost_per_share=150.0, acquired=date(2022, 3, 1), order=2)
NEW_GAIN = Lot(shares=6, cost_per_share=90.0, acquired=date(2026, 5, 1), order=3)
LOTS = [OLD_GAIN, NEW_LOSS, OLD_LOSS, NEW_GAIN]


def picked(sales):
    """Return (lot order, shares) for each sale, to compare against expected picks."""
    return [(sale.lot.order, sale.shares) for sale in sales]


@pytest.mark.parametrize(
    "acquired, sold, term",
    [
        (date(2025, 2, 5), date(2026, 2, 4), "short"),
        (date(2025, 2, 5), date(2026, 2, 5), "short"),  # The anniversary itself is still short term.
        (date(2025, 2, 5), date(2026, 2, 6), "long"),
        (date(2024, 2, 29), date(2025, 2, 28), "short"),  # Leap day: anniversary is Feb 28.
        (date(2024, 2, 29), date(2025, 3, 1), "long"),
        (date(2026, 11, 12), date(2026, 11, 12), "short"),  # Bought and sold the same day.
    ],
)
def test_holding_term(acquired, sold, term):
    assert holding_term(acquired, sold) == term


def test_fifo_sells_oldest_first():
    assert picked(select_lots(LOTS, 12, PRICE, SALE, "fifo")) == [(0, 10), (2, 2)]


def test_lifo_sells_newest_first():
    assert picked(select_lots(LOTS, 12, PRICE, SALE, "lifo")) == [(1, 5), (3, 6), (2, 1)]


def test_hifo_sells_highest_cost_first():
    assert picked(select_lots(LOTS, 12, PRICE, SALE, "hifo")) == [(2, 4), (1, 5), (3, 3)]


def test_min_tax_sells_largest_tax_saving_first():
    # Tax per share at 24% short / 15% long: NEW_LOSS -2.40, OLD_LOSS -7.50,
    # NEW_GAIN +2.40, OLD_GAIN +12.00.
    assert picked(select_lots(LOTS, 12, PRICE, SALE, "min_tax")) == [(2, 4), (1, 5), (3, 3)]


def test_min_tax_follows_the_given_rates():
    # At 37% short / 4% long the old gain's tax per share (80 * 0.04 = 3.20)
    # is below the recent gain's (10 * 0.37 = 3.70), so it's sold first.
    sales = select_lots(LOTS, 12, PRICE, SALE, "min_tax", {"short_term": 0.37, "long_term": 0.04})
    assert picked(sales) == [(1, 5), (2, 4), (0, 3)]


def test_min_tax_is_the_default():
    assert select_lots(LOTS, 12, PRICE, SALE) == select_lots(LOTS, 12, PRICE, SALE, "min_tax")


def test_ties_keep_request_order():
    same = [Lot(1, 50.0, date(2020, 1, 1), order=i) for i in range(3)]
    assert [s.lot.order for s in select_lots(same, 3, PRICE, SALE, "hifo")] == [0, 1, 2]


def test_selling_everything_uses_each_lot_exactly():
    sales = select_lots(LOTS, 25, PRICE, SALE, "min_tax")
    assert sorted(picked(sales)) == [(0, 10), (1, 5), (2, 4), (3, 6)]


def test_float_rounding_does_not_leave_a_sliver():
    lots = [Lot(0.1, 1.0, date(2020, 1, 1), 0), Lot(0.2, 1.0, date(2020, 1, 2), 1)]
    assert picked(select_lots(lots, 0.3, 2.0, SALE, "fifo")) == [(0, 0.1), (1, 0.2)]


def test_selling_more_than_held_is_an_error():
    with pytest.raises(ValueError):
        select_lots(LOTS, 26, PRICE, SALE)


def test_unknown_method_is_an_error():
    with pytest.raises(ValueError):
        select_lots(LOTS, 1, PRICE, SALE, "average")


def test_gain_and_term_per_sale():
    sale = select_lots([OLD_GAIN, NEW_LOSS], 12, PRICE, SALE, "fifo")
    assert [(s.term, s.gain) for s in sale] == [("long", 800.0), ("short", -20.0)]


def test_wash_sale_flags_loss_with_recent_purchase():
    recent = Lot(1, 95.0, date(2026, 10, 13), order=9)  # 30 days before the sale.
    sales = select_lots([OLD_LOSS], 4, PRICE, SALE)
    assert wash_sale_risk([OLD_LOSS, recent], sales, SALE) == (-200.0, [date(2026, 10, 13)])


def test_wash_sale_ignores_purchases_over_30_days_before():
    older = Lot(1, 95.0, date(2026, 10, 12), order=9)  # 31 days before the sale.
    sales = select_lots([OLD_LOSS], 4, PRICE, SALE)
    assert wash_sale_risk([OLD_LOSS, older], sales, SALE) == (0.0, [])


def test_wash_sale_ignores_gains():
    recent = Lot(1, 95.0, date(2026, 11, 1), order=9)
    sales = select_lots([OLD_GAIN], 10, PRICE, SALE)
    assert wash_sale_risk([OLD_GAIN, recent], sales, SALE) == (0.0, [])


def test_a_loss_lot_is_not_its_own_replacement():
    recent_loss = Lot(2, 120.0, date(2026, 11, 1), order=0)
    sales = select_lots([recent_loss], 2, PRICE, SALE)
    assert wash_sale_risk([recent_loss], sales, SALE) == (0.0, [])
