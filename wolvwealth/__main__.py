"""Command-line entry point: python -m wolvwealth <command>.

Works from any directory; data files are resolved from the install location.
"""
import argparse
import os
import sys


def main():
    """Run a wolvwealth maintenance command."""
    parser = argparse.ArgumentParser(prog="python -m wolvwealth", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    refresh = sub.add_parser("refresh-data", help="Pull market prices up to today into historical_prices.csv")
    refresh.add_argument(
        "--full",
        action="store_true",
        help="Re-download the entire 2006-to-today history (correct after splits/dividends) "
        "instead of appending only the missing recent rows",
    )
    args = parser.parse_args()

    # Importing the package would trigger the startup auto-refresh; this
    # command manages the refresh itself.
    os.environ["WOLVWEALTH_NO_AUTO_REFRESH"] = "1"
    import wolvwealth

    state = wolvwealth.state
    last = state.HISTORICAL_PRICES.index[-1].date()
    print(f"Universe: {len(state.TICKER_UNIVERSE)} tickers. Cached prices end {last}.")
    if args.full:
        print("Full re-download, 2006 -> today. This takes a while and rewrites historical_prices.csv ...")
        state.save_historical_prices()
        state.load_historical_prices()
        changed = True
    else:
        changed = state.update_historical_prices()
    if changed:
        print(f"Done. Prices now end {state.HISTORICAL_PRICES.index[-1].date()}.")
    else:
        print("Already up to date.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
