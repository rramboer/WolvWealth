"""A flagged price move gets a second opinion before it costs anything.

The plausibility check escalates; it does not convict. For each newly
flagged ticker, in order:

1. **Yahoo's own split calendar.** A move that matches a split ratio on its
   date is the split applied inconsistently -- an adjustment artifact, not a
   price. The verdict is a basis failure, which the existing repair
   machinery answers by re-downloading the history whole on the next
   refresh; nothing is disbelieved about the company.
2. **An independent price source** (stooq, by default). A ratio across two
   dates is basis-free, so the sources are asked about the very same pair of
   days. Agreement means the move is real: it is recorded as accepted, with
   the evidence, and never flagged again. Disagreement means the flagged
   prices are confirmed wrong -- and for a move the price *stayed* at, that
   the newer closes are the wrong side, so the ticker is excluded outright
   rather than trusted from the jump on.
3. **No answer** -- source not configured, unreachable, or missing the
   ticker -- leaves the single-source verdict exactly as it was, with the
   reason saying it could not be checked.

A human acceptance always wins: an accepted date is never flagged, so it
never reaches this module. Everything here consults the network, at most
once per flagged ticker per source, capped per refresh; the pure comparison
rules live in ``price_checks``.
"""

from wolvwealth.domain.market import classify_implausible
from wolvwealth.services import price_checks

#: Flagged tickers one refresh will spend network requests on. Routine
#: refreshes flag zero or a handful; a burst beyond this is left on the
#: single-source verdict (safe: scoped and overridable) rather than turned
#: into a request storm against sources doing us a favour.
MAX_TICKERS = 20


class SecondOpinions:  # pylint: disable=too-few-public-methods  # one entry point, four wired collaborators
    """Reviews one refresh's findings against the split calendar and a second price source.

    ``review`` is deliberately the only door: the caller hands over a
    refresh's findings and gets verdicts back, and everything else is the
    private mechanics of asking the two sources.
    """

    def __init__(self, verifier, splits_source, accept, log) -> None:
        """Wire the sources (either may be None: not consulted), the accepted-move recorder, and the log."""
        self.verifier = verifier
        self.splits_source = splits_source
        self.accept = accept
        self.log = log
        self.source = getattr(verifier, "source_name", "a second source")

    def review(self, found: dict, view, accepted: dict, since, whole) -> tuple:
        """Weigh each finding against the outside evidence.

        Returns ``(findings to record, basis artifacts)`` -- the second maps
        tickers whose flagged move was really a misapplied split to
        ``{"reason", "valuation"}``, for the caller to treat exactly like a
        failed split repair (full re-download on the next refresh).
        """
        if not found or (self.verifier is None and self.splits_source is None):
            return found, {}
        verified, artifacts = {}, {}
        for position, (ticker, finding) in enumerate(found.items()):
            if position >= MAX_TICKERS:
                self.log(
                    f"wolvwealth: {len(found) - MAX_TICKERS} more flagged ticker(s) were not checked against "
                    "outside sources (per-refresh budget); their single-source exclusions stand."
                )
                verified.update(dict(list(found.items())[position:]))
                break
            moves = self._moves(view[ticker], finding)
            artifact = self._split_artifact(ticker, view[ticker], moves)
            if artifact is not None:
                artifacts[ticker] = artifact
                continue
            outcome = self._corroborate(
                ticker, view[ticker], finding, moves, accepted, since if ticker not in whole else None
            )
            if outcome is not None:
                verified[ticker] = outcome
        return verified, artifacts

    @staticmethod
    def _moves(closes, finding: dict) -> dict:
        """The flagged moves behind a finding, as ``{day: (previous day, ratio)}``."""
        moves = {}
        for day in price_checks.finding_events(finding):
            observed = price_checks.observed_move(closes, day)
            if observed is not None:
                moves[day] = observed
        return moves

    def _split_artifact(self, ticker: str, closes, moves: dict):
        """A basis-failure record when a flagged move matches the ticker's split calendar, else None."""
        if self.splits_source is None or not moves:
            return None
        splits = self.splits_source(ticker)
        if splits is None:
            return None
        for day, (_previous, ratio) in moves.items():
            size = price_checks.matching_split(splits, day, ratio)
            if size is None:
                continue
            reason = (
                f"{price_checks.BASIS_FAILURE_REASON}: its {ratio:.2f}x move on {day} matches the {size:g}:1 "
                "split recorded there, applied to one side of the history and not the other"
            )
            self.log(
                f"wolvwealth: {ticker}'s flagged move on {day} is its {size:g}:1 split applied inconsistently, "
                "not a price; its full history will be re-downloaded on the next refresh."
            )
            # The newest close is the side Yahoo serves live, hence today's
            # basis: the only number a holding can be valued at meanwhile.
            tail = closes.dropna()
            valuation = {"close": float(tail.iloc[-1]), "as_of": str(tail.index[-1].date())} if len(tail) else None
            return {"reason": reason, "valuation": valuation}
        return None

    def _corroborate(self, ticker: str, closes, finding: dict, moves: dict, accepted: dict, since):
        """Ask the second source about each flagged move and return the finding to record, or None if all real."""
        second = self.verifier(ticker) if self.verifier is not None else None
        verdicts = {
            day: price_checks.second_opinion(second, previous, day, ratio) for day, (previous, ratio) in moves.items()
        }
        real = sorted(day for day, verdict in verdicts.items() if verdict == "real")
        if real:
            notes = {day: f"moves the same way in {self.source}'s data" for day in real}
            self.accept(ticker, notes)
            accepted.setdefault(ticker, {}).update(notes)
            self.log(
                f"wolvwealth: {ticker}'s flagged move(s) on {', '.join(real)} are in {self.source}'s data too, "
                "so they are real and kept (recorded in accepted_moves.json)."
            )
            finding = classify_implausible(closes, accepted[ticker], since)
            if finding is None:
                return None
        return self._with_evidence(finding, verdicts)

    def _with_evidence(self, finding: dict, verdicts: dict) -> dict:
        """Fold what the second source said into the verdict that stands.

        A persistent move the source disputes means the *newer* closes are
        the wrong side, so trusting the history from the jump onward -- the
        single-source verdict -- would serve exactly the prices the evidence
        is against. That escalates to excluding the ticker; valuation then
        falls back to the last close before the jump, the side both sources
        agree on.
        """
        denied = sorted(day for day, verdict in verdicts.items() if verdict == "corrupt")
        if not denied:
            if self.verifier is not None and any(verdict is None for verdict in verdicts.values()):
                finding["reason"] += f"; {self.source} could not be checked for a second opinion"
            return finding
        evidence = f"; {self.source} does not show the move on {', '.join(denied)}, so those prices are wrong"
        if finding.get("untrusted_before") in denied:
            return {
                "scope": "ticker",
                "reason": finding["reason"]
                + f"; {self.source} disagrees with the closes from {finding['untrusted_before']} on, "
                "so none of them can be trusted",
            }
        finding["reason"] += evidence
        return finding
