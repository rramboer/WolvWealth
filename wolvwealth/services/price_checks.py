"""What the app believes about its own price data, and how little it throws away over a doubt.

The plausibility rule that feeds this module cannot be right every time.
Some 2,590 tickers over twenty years is around thirteen million closes, so
genuine one-day moves past 5x -- a biotech trial result, a collapse, a
post-bankruptcy relisting -- are not freak events but a certainty, and no
rule reading a single price series can tell one from a corrupt print. The
design therefore assumes false positives and makes them cheap, rather than
pretending to avoid them:

- **The verdict is scoped.** An exclusion names the days it distrusts, not
  the company. One bad print costs one close; a move the price stayed at
  costs the history before it. Excluding a ticker outright is the last
  resort, for when nothing usable is left or its whole column is on a
  superseded adjustment basis.
- **The verdict is reversible.** Nothing is deleted. Exclusions are a view
  over the archive (``apply_exclusions``), re-applied on every load, so a
  later download -- or a person -- can undo one by re-reading the file.
- **The verdict is adjudicable.** ``accepted_moves.json`` records the dates
  a human judged real, and no refresh re-decides them.

The functions here are pure: the record in, a view or a new record out. The
I/O and the download orchestration live in ``market_data``.
"""

import pandas as pd

from wolvwealth.domain.market import last_believable_close

#: Opening words of the exclusion reason recorded when a split/dividend
#: re-download failed or came back truncated. Such a ticker is excluded while
#: its cached history sits on a superseded adjustment basis, so a later
#: refresh that does replace the history lifts exactly these entries --
#: never one recorded for implausible prices, which a new basis does not cure.
BASIS_FAILURE_REASON = "changed adjustment basis"


def scope_of(entry: dict) -> str:
    """Return an entry's scope, defaulting to the strongest.

    A record written before partial exclusions existed has no scope and
    excluded the whole ticker, so that is what an absent or unknown value
    has to mean -- never the weaker reading, which would quietly start
    serving prices an older version had refused.
    """
    return "partial" if entry.get("scope") == "partial" else "ticker"


def apply_exclusions(prices: pd.DataFrame, record: dict) -> pd.DataFrame:
    """Return the snapshot view of the archive: excluded prices blanked, the archive itself untouched.

    Blanking happens here, on the way to the optimizer, and never on the
    way to disk. An excluded ticker therefore keeps every close it ever
    had, so lifting the exclusion is a matter of re-reading the file
    rather than re-downloading a history Yahoo may not serve again.

    How much is blanked is the entry's scope. Whole-ticker is the last
    resort, not the default: one bad print blanks that one cell, and a move
    the price stayed at blanks only the history before it, so a ticker
    whose 2009 was unbelievable still trades on its 2010s.
    """
    served = None
    for ticker, entry in record.items():
        if ticker not in prices.columns:
            continue
        if served is None:
            served = prices.copy()
        if scope_of(entry) == "ticker":
            served[ticker] = float("nan")
            continue
        before = entry.get("untrusted_before")
        if before:
            served.loc[served.index < pd.Timestamp(before), ticker] = float("nan")
        masked = [pd.Timestamp(day) for day in entry.get("masked_days", ())]
        inside = [day for day in masked if day in served.index]
        if inside:
            served.loc[inside, ticker] = float("nan")
    return prices if served is None else served


def frozen_valuations(prices: pd.DataFrame, record: dict) -> dict:
    """Each excluded-outright ticker's best trustworthy close, as ``{ticker: (close, date)}``.

    So a held position in one is still valued (and frozen) instead of
    failing the whole request. The two exclusion reasons need different
    prices, and using the wrong one misstates the position:

    - *Split or dividend re-download failed.* The cached history is on a
      superseded basis -- after a 2:1 split its closes are twice the real
      price -- so it must not be used at all. ``basis_entry`` stored a
      close from the download that detected the change, which is on the
      current basis.
    - *Implausible move.* The download is what is distrusted, so the cached
      history is used, stopping before the first flagged move.

    Only whole-ticker exclusions need this: a partially excluded ticker
    still has closes in the snapshot and is valued like any other. Only
    tickers with such a close appear; one with neither genuinely cannot be
    valued.
    """
    frozen = {}
    for ticker, entry in record.items():
        if scope_of(entry) != "ticker":
            continue
        valuation = entry.get("valuation")
        if valuation:
            frozen[ticker] = (valuation["close"], pd.Timestamp(valuation["as_of"]).date())
        elif ticker in prices.columns:
            believable = last_believable_close(prices[ticker])
            if believable is not None:
                frozen[ticker] = believable
    return frozen


def basis_entry(failure: dict, previous_entry, detected: str) -> dict:
    """Exclusion entry for a failed re-basing, carrying a close on the current adjustment basis.

    This refresh's own download first; failing that (nothing came back for
    the ticker at all), whatever an earlier one managed to record, which is
    still on the current basis -- the cached history is not, which is the
    whole reason the ticker is excluded.
    """
    # Whole-ticker: a superseded basis is wrong for every close in the
    # column at once, which is not something trimming a date range fixes.
    entry = {"reason": failure["reason"], "detected": detected, "scope": "ticker"}
    valuation = failure.get("valuation") or (previous_entry or {}).get("valuation")
    if valuation:
        entry["valuation"] = valuation
    return entry


def merge_exclusion(existing, finding: dict, detected: str) -> dict:
    """Combine a new finding with what is already excluded for that ticker, keeping the stricter of each.

    A finding is judged against the view, so it describes what is *still*
    wrong once the existing exclusion is applied -- it is an addition to
    that exclusion, never a replacement for it. Replacing would un-mask the
    very print whose masking is the reason the new finding looks the way it
    does.
    """
    existing = existing or {}
    # Only an entry that actually exists can widen the scope. Reading a
    # default off an absent one would turn every partial finding into a
    # whole-ticker exclusion, which is the behaviour this replaces.
    scopes = {finding["scope"]} | ({scope_of(existing)} if existing else set())
    scope = "ticker" if "ticker" in scopes else "partial"
    entry = {"reason": finding["reason"], "detected": detected, "scope": scope}
    if scope == "partial":
        # ISO dates, so the later string is the later day: the stricter cut.
        cuts = [day for day in (existing.get("untrusted_before"), finding.get("untrusted_before")) if day]
        if cuts:
            entry["untrusted_before"] = max(cuts)
        masked = sorted(set(existing.get("masked_days", ())) | set(finding.get("masked_days", ())))
        if masked:
            entry["masked_days"] = masked
    return entry


def is_basis_failure(entry) -> bool:
    """Return true if an entry was recorded because a split/dividend re-download failed."""
    return bool(entry) and entry["reason"].startswith(BASIS_FAILURE_REASON)


def lift_cured_basis_failures(record: dict, recovered) -> list:
    """Drop ``recovered`` tickers from ``record`` where they were excluded only for a failed re-basing.

    The cure is specific: their history now exists whole on the current
    adjustment basis, which is exactly what the exclusion was waiting for.
    An entry recorded for implausible prices is left alone -- a split says
    nothing about whether those prices were believable. Returns the lifted
    tickers rather than announcing them: "back in allocations" is only true
    of a replacement history that also passes the implausibility check,
    which the caller runs after this.
    """
    lifted = []
    for ticker in recovered:
        if is_basis_failure(record.get(ticker)):
            del record[ticker]
            lifted.append(ticker)
    return lifted
