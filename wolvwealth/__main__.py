"""Command-line entry point: ``wolvwealth <command>`` (or ``python -m wolvwealth``).

Works from any directory; data files are resolved from the install location.

    wolvwealth serve            start the app on http://localhost:8000 and open a browser
    wolvwealth refresh-data     pull market prices up to today
    wolvwealth reset-db         erase every workspace and start from the demo seed
    wolvwealth dump-db          print all workspaces, portfolios, and holdings
"""
import argparse
import os
import sys
import threading
import webbrowser

from wolvwealth.services.container import Services

DEFAULT_PORT = 8000


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser (separate so tests can exercise it)."""
    parser = argparse.ArgumentParser(prog="wolvwealth", description="Local-first portfolio optimizer.")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="Run the app on localhost and open it in your browser")
    serve.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"TCP port (default {DEFAULT_PORT})")
    serve.add_argument("--no-browser", action="store_true", help="Do not open a browser tab")
    serve.add_argument("--dev", action="store_true", help="Flask debug mode: auto-reload on code changes")

    refresh = sub.add_parser("refresh-data", help="Pull market prices up to today into var/historical_prices.parquet")
    refresh.add_argument(
        "--full",
        action="store_true",
        help="Re-download the entire 2006-to-today history (correct after splits/dividends) "
        "instead of appending only the missing recent rows",
    )

    sub.add_parser("reset-db", help="Delete var/wolvwealth.sqlite3 and recreate it with the demo seed")
    sub.add_parser("dump-db", help="Print every workspace, portfolio, and holding")
    return parser


def cmd_serve(args, services: Services) -> int:
    """Load prices (refreshing stale ones), then run Flask on localhost only (no auth, so never elsewhere)."""
    from wolvwealth.web import create_app  # pylint: disable=import-outside-toplevel

    in_reloader_child = bool(os.environ.get("WERKZEUG_RUN_MAIN"))
    if not in_reloader_child:
        services.market.data  # pylint: disable=pointless-statement  # load (or build) the cache up front
        if services.settings.auto_refresh:
            services.market.auto_refresh_if_stale(services.settings.auto_refresh_max_age_days)
    app = create_app(services)
    url = f"http://localhost:{args.port}/"
    print(f"WolvWealth is running at {url}  (Ctrl-C to stop)")
    if not args.no_browser and not in_reloader_child:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    app.run(host="127.0.0.1", port=args.port, debug=args.dev)
    return 0


def cmd_refresh_data(args, services: Services) -> int:
    """Refresh the price cache, incrementally or in full."""
    market = services.market
    print(f"Universe: {len(market.data.universe)} tickers. Cached prices end {market.data.as_of}.")
    if args.full:
        print("Full re-download, 2006 -> today. This takes a while and rewrites the price cache ...")
    if market.refresh(full=args.full):
        print(f"Done. Prices now end {market.data.as_of}.")
    else:
        print("Already up to date.")
    return 0


def cmd_reset_db(_args, services: Services) -> int:
    """Erase all workspaces by rebuilding the database from sql/."""
    services.database.reset()
    print(f"Database recreated at {services.database.path}. All workspaces erased; the demo workspace is back.")
    return 0


def cmd_dump_db(_args, services: Services) -> int:
    """Print the contents of every table."""
    with services.database.connect() as connection:
        for table, rows in services.repository(connection).dump().items():
            print(f"== {table} ==")
            for row in rows:
                print(row)
            print()
    return 0


COMMANDS = {
    "serve": cmd_serve,
    "refresh-data": cmd_refresh_data,
    "reset-db": cmd_reset_db,
    "dump-db": cmd_dump_db,
}


def main(argv=None, services: Services = None) -> int:
    """Parse arguments and dispatch to a command."""
    args = build_parser().parse_args(argv)
    return COMMANDS[args.command](args, services or Services.build())


if __name__ == "__main__":
    sys.exit(main())
