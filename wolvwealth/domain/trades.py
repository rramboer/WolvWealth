"""Trade list: the moves that turn the holdings you have into the portfolio you want.

The optimizer answers "what should I hold"; an agent driving a broker needs
"what should I do". This is that difference, expressed as whole trades an
order API can take directly, so the agent never has to diff two allocations
itself and never has to guess which price the plan assumed.
"""

#: Share counts are reported to this many decimals, matching the optimizer's
#: own output. A delta that rounds to zero here is not a trade.
SHARE_PRECISION = 4


def plan(holdings: dict, target: dict, prices: dict) -> list:
    """Return the trades taking ``holdings`` to ``target``, sells first.

    ``holdings`` and ``target`` are ticker-to-share-count maps and ``prices``
    is ticker-to-price. A ticker held but absent from the target is sold off
    entirely; one in the target but not held is bought outright.

    Sells lead, each group ordered by descending value, because an agent
    executing the list in order frees the cash before it spends it. Share
    counts are positive; ``action`` carries the direction.
    """
    moves = []
    for ticker in set(holdings) | set(target):
        delta = round(target.get(ticker, 0.0) - holdings.get(ticker, 0.0), SHARE_PRECISION)
        if delta == 0:
            continue
        price = prices[ticker]
        moves.append(
            {
                "ticker": ticker,
                "action": "buy" if delta > 0 else "sell",
                "shares": abs(delta),
                "price": round(price, 2),
                "value": round(abs(delta) * price, 2),
            }
        )
    moves.sort(key=lambda move: (move["action"] != "sell", -move["value"], move["ticker"]))
    return moves
