"""Command-line entry point: ``wolvwealth <command>`` (or ``python -m wolvwealth``).

Works from any directory; data files are resolved from the install location.

    wolvwealth serve            start the app on http://localhost:8000 and open a browser
    wolvwealth optimize         max-Sharpe allocation and trade list, printed to the terminal (or --json)
    wolvwealth mcp              serve the MCP tools over stdio for an AI app (--config prints the setup snippet)
    wolvwealth refresh-data     pull market prices up to today
    wolvwealth reset-db         erase every workspace and start from the demo seed
    wolvwealth dump-db          print all workspaces, portfolios, and holdings
"""

import argparse
import json
import os
import pathlib
import re
import sys
import textwrap
import threading
import webbrowser

from wolvwealth.domain import holdings_parse
from wolvwealth.domain.errors import DomainError, ValidationError
from wolvwealth.services import optimize, risk_free
from wolvwealth.services.container import Services

DEFAULT_PORT = 8000
#: ``optimize`` flags that map one-to-one onto ``constraints.<name>`` in the API body.
CONSTRAINT_FLAGS = ("max_positions", "max_weight", "min_universal_weight", "max_turnover", "cash_reserve")
#: How the report describes where the risk-free rate came from.
RATE_SOURCE_LABELS = {
    "request": "from --risk-free-rate",
    "tbill": "the current 13-week T-bill yield",
    "portfolio": "stored on the portfolio",
    "default": "none given, so 0% assumed",
}


def _port(raw: str) -> int:
    """Parse a TCP port for argparse, which turns the ValueError into a usage error."""
    port = int(raw)
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError(f"{raw} is not a TCP port (1-65535)")
    return port


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser (separate so tests can exercise it)."""
    parser = argparse.ArgumentParser(prog="wolvwealth", description="Local-first portfolio optimizer.")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="Run the app on localhost and open it in your browser")
    serve.add_argument("--port", type=_port, default=DEFAULT_PORT, help=f"TCP port, 1-65535 (default {DEFAULT_PORT})")
    serve.add_argument("--no-browser", action="store_true", help="Do not open a browser tab")
    serve.add_argument("--dev", action="store_true", help="Flask debug mode: auto-reload on code changes")

    _add_optimize_parser(sub)

    refresh = sub.add_parser("refresh-data", help="Pull market prices up to today into var/historical_prices.parquet")
    refresh.add_argument(
        "--full",
        action="store_true",
        help="Re-download the entire 2006-to-today history (recovers throttled tickers) "
        "instead of appending only the missing recent rows",
    )

    mcp = sub.add_parser(
        "mcp",
        help="Serve the MCP tools over stdio for an AI app (Claude Desktop, Claude Code, Cursor, ...)",
        description="Speaks the Model Context Protocol on stdin/stdout. AI apps start this themselves from their "
        "configuration; you do not run it by hand. `wolvwealth mcp --config` prints that configuration.",
    )
    mcp.add_argument("--config", action="store_true", help="Print the JSON an AI app needs to start this server")

    accept = sub.add_parser(
        "accept-price",
        help="Record that a flagged one-day price move was real, so it stops excluding prices",
        description="The plausibility check cannot tell a real one-day collapse or repricing from corrupt "
        "data, because a single price series does not contain the answer. Use this when you know a flagged "
        "move really happened: the date is never flagged again, by any later refresh, and the ticker is "
        "re-judged immediately. `refresh-data` prints the ticker and date to pass here.",
    )
    accept.add_argument("ticker", help="Ticker whose move was real, e.g. CVAC")
    accept.add_argument("date", help="Day of the move, YYYY-MM-DD, as printed by refresh-data")

    sub.add_parser("reset-db", help="Delete var/wolvwealth.sqlite3 and recreate it with the demo seed")
    sub.add_parser("dump-db", help="Print every workspace, portfolio, and holding")
    return parser


def _rate_argument(raw: str):
    """Accept a decimal fraction or the word ``tbill`` for --risk-free-rate."""
    if raw.strip().lower() == risk_free.TBILL:
        return risk_free.TBILL
    try:
        return float(raw)
    except ValueError as err:
        raise argparse.ArgumentTypeError(f"{raw!r} is not a number (0.04 means 4%) or 'tbill'") from err


def _add_optimize_parser(sub) -> None:
    """Add the ``optimize`` subparser: the API's request body, one flag per field."""
    parser = sub.add_parser(
        "optimize",
        help="Max-Sharpe allocation and the trades to reach it, printed to the terminal",
        description="Optimize a stored portfolio, or cash and holdings given here, without a running server. "
        "Flags mirror the fields of POST /api/optimize/ (see API.md); --json prints that same response body. "
        "Prices come from the local cache (run `wolvwealth refresh-data` for current ones).",
    )
    what = parser.add_argument_group("what to optimize")
    what.add_argument("--workspace", help="Workspace holding --portfolio")
    what.add_argument("--portfolio", help="Name of a stored portfolio to load cash and holdings from")
    what.add_argument("--cash", type=float, metavar="USD", help="Cash to invest (overrides the stored amount)")
    what.add_argument("--holdings", metavar="LIST", help='Held shares as TICKER=SHARES pairs, e.g. "AAPL=10,MSFT=5"')
    what.add_argument(
        "--holdings-file",
        metavar="FILE",
        help="Holdings from a file: TICKER,SHARES lines or a brokerage positions CSV; a CASH row sets cash",
    )
    parser.add_argument(
        "--universe",
        nargs="+",
        metavar="ENTRY",
        help="Aliases (top20 ... top500) and/or tickers to allocate across; held tickers are always added so "
        "keeping them is possible. Default: top500",
    )
    parser.add_argument(
        "--min-history-days",
        type=int,
        metavar="N",
        help="Trading days of price history a ticker needs to be in the universe. Default 252 (a year): a newly "
        "listed name's few weeks of returns, extrapolated to a year, would otherwise dominate the result",
    )
    constraints = parser.add_argument_group("constraints (same meaning and ranges as the API)")
    constraints.add_argument("--max-positions", type=int, metavar="N", help="Largest number of holdings")
    constraints.add_argument("--max-weight", type=float, metavar="F", help="Per-asset cap, 0-1")
    constraints.add_argument("--min-universal-weight", type=float, metavar="F", help="Floor on every universe ticker")
    constraints.add_argument(
        "--max-turnover", type=float, metavar="F", help="Largest fraction of the portfolio the plan may sell, 0-1"
    )
    constraints.add_argument("--cash-reserve", type=float, metavar="F", help="Fraction to leave in cash, 0-1")
    parser.add_argument(
        "--risk-free-rate",
        type=_rate_argument,
        metavar="RATE",
        help="Annual yield the idle cash earns, as a fraction (0.04 is 4%%), or 'tbill'. "
        "Default: the stored portfolio's rate, else 0%% (the report says so)",
    )
    parser.add_argument("--json", action="store_true", help="Print the API response body instead of a report")


def cmd_serve(args, services: Services) -> int:
    """Load prices, start a refresh if they are stale, then run Flask on localhost only (no auth)."""
    from wolvwealth.web import create_app  # pylint: disable=import-outside-toplevel

    in_reloader_child = bool(os.environ.get("WERKZEUG_RUN_MAIN"))
    if not in_reloader_child:
        services.market.data  # pylint: disable=pointless-statement  # load (or build) the cache up front
        if services.settings.auto_refresh:
            # In the background: a stale refresh is minutes of downloading and
            # recomputing, and the cached prices are good enough to serve
            # until it lands. `wolvwealth refresh-data` stays synchronous.
            services.market.start_background_refresh(services.settings.auto_refresh_max_age_days)
    app = create_app(services)
    url = f"http://localhost:{args.port}/"
    print(f"WolvWealth is running at {url}  (Ctrl-C to stop)")
    if not args.no_browser and not in_reloader_child:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    app.run(host="127.0.0.1", port=args.port, debug=args.dev)
    return 0


def _holdings_argument(args) -> tuple:
    """Parse --holdings / --holdings-file into ``(holdings, cash)``; cash is set only by a CASH row."""
    if args.holdings and args.holdings_file:
        raise ValidationError("Pass --holdings or --holdings-file, not both.")
    if args.holdings_file:
        try:
            text = pathlib.Path(args.holdings_file).read_text(encoding="utf-8")
        except OSError as err:
            raise ValidationError(f"Cannot read --holdings-file {args.holdings_file}: {err.strerror}.") from err
        except UnicodeDecodeError as err:
            raise ValidationError(
                f"Cannot read --holdings-file {args.holdings_file}: it is not UTF-8 text. Export it as CSV."
            ) from err
    elif args.holdings:
        # One pair per line is what the parser reads; pairs arrive on one line
        # here, separated by commas or spaces.
        text = "\n".join(re.split(r"[,\s]+", args.holdings.strip()))
    else:
        return {}, None
    parsed = holdings_parse.parse_holdings(text)
    if parsed.errors:
        problems = "; ".join(parsed.errors + parsed.ignored)
        raise ValidationError(f"Could not read the holdings ({problems}). Expected TICKER=SHARES pairs or a CSV.")
    return parsed.holdings, parsed.cash


def _optimize_payload(args) -> dict:
    """Translate the flags into the API's request body, so the CLI and the API cannot mean different things."""
    payload = {}
    if args.portfolio is not None:
        if args.workspace is None:
            raise ValidationError("--portfolio needs --workspace: the shell has no open workspace to fall back on.")
        payload["workspace"], payload["portfolio"] = args.workspace, args.portfolio
    elif args.workspace is not None:
        raise ValidationError("--workspace does nothing without --portfolio.")
    holdings, cash = _holdings_argument(args)
    if holdings:
        payload["initial_holdings"] = holdings
    if args.cash is not None:
        cash = args.cash
    if cash is not None:
        payload["initial_cash"] = cash
    if args.universe:
        payload["universe"] = list(args.universe)
    if args.min_history_days is not None:
        payload["min_history_days"] = args.min_history_days
    constraints = {name: getattr(args, name) for name in CONSTRAINT_FLAGS if getattr(args, name) is not None}
    if constraints:
        payload["constraints"] = constraints
    if args.risk_free_rate is not None:
        payload["risk_free_rate"] = args.risk_free_rate
    return payload


def _money(value: float) -> str:
    return f"${value:,.2f}"


def render_report(result: dict, universe: list, portfolio=None) -> str:
    """Lay out an optimize response for a person reading a terminal."""
    what = f"{portfolio.workspace}/{portfolio.name}" if portfolio is not None else "the given cash and holdings"
    rate = result["risk_free_rate"]
    lines = [
        f"Max-Sharpe allocation for {what}, priced at the {result['as_of']} close (cached, not live quotes).",
        f"Universe: {', '.join(universe)}",
        f"Risk-free rate: {rate['value']:.2%} ({RATE_SOURCE_LABELS[rate['source']]})",
    ]
    if "note" in rate:
        lines.append(textwrap.fill(rate["note"], 110, initial_indent="  ", subsequent_indent="  "))
    lines += ["", "Target allocation", f"  {'TICKER':<8}{'WEIGHT':>8}{'SHARES':>12}{'VALUE':>14}"]
    rows = sorted(result["optimized_portfolio"].items(), key=lambda kv: kv[1]["value"], reverse=True)
    for ticker, row in rows:
        lines.append(f"  {ticker:<8}{row['percent_weight']:>7.2f}%{row['shares']:>12.4f}{_money(row['value']):>14}")
    cash = result["cash"]
    if cash["value"] > 0:
        lines.append(f"  {'CASH':<8}{cash['percent_weight']:>7.2f}%{'':>12}{_money(cash['value']):>14}")
    if "unoptimized" in result:
        kept = result["unoptimized"]["positions"]
        names = ", ".join(f"{ticker} ({row['history_days']} days of history)" for ticker, row in sorted(kept.items()))
        lines += ["", f"Kept as held, not optimized: {names}"]
        lines.append(textwrap.fill(result["unoptimized"]["note"], 110, initial_indent="  ", subsequent_indent="  "))
    lines.append("")
    lines.append("Trades to get there, sells first" if result["trades"] else "No trades: already at the target.")
    for move in result["trades"]:
        lines.append(
            f"  {move['action'].upper():<6}{move['ticker']:<8}{move['shares']:>12.4f}"
            f" @ {_money(move['price']):>10} = {_money(move['value']):>14}"
        )
    metrics = result["metrics"]
    sharpe = "n/a" if metrics["sharpe_ratio"] is None else f"{metrics['sharpe_ratio']:.2f}"
    lines += [
        "",
        f"Portfolio value {_money(metrics['portfolio_value'])}   "
        f"expected annual return {metrics['expected_annual_return']:.1%}   "
        f"volatility {metrics['annual_volatility']:.1%}   Sharpe {sharpe}   "
        f"assets {metrics['num_assets']}   sold {metrics['turnover']:.1%}",
    ]
    return "\n".join(lines)


def _report_error(error: DomainError, as_json: bool) -> int:
    """Print a domain error the way the chosen output mode expects, and return the exit status."""
    if as_json:
        body = {"error": {"message": error.message}}
        if error.details:
            body["error"]["payload"] = error.details
        print(json.dumps(body, indent=2))
    else:
        print(f"wolvwealth: {error.message}", file=sys.stderr)
    return 1


def cmd_optimize(args, services: Services) -> int:
    """Solve against the cached prices and print a report (or the API body). Never refreshes."""
    market = services.market.data
    try:
        payload = _optimize_payload(args)
        with services.database.connect() as connection:
            repo = services.repository(connection)
            result, universe, portfolio = optimize.optimize_keeping_held(payload, market, repo, services.market.tbill)
    except DomainError as error:
        return _report_error(error, args.json)
    age = market.age_days()
    if age > services.settings.auto_refresh_max_age_days:
        # On stderr, so --json output stays parseable; as_of is in the body too.
        print(
            f"wolvwealth: cached prices are {age} days old (as of {market.as_of}); "
            "run `wolvwealth refresh-data` for current ones.",
            file=sys.stderr,
        )
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(render_report(result, universe, portfolio))
    return 0


def cmd_refresh_data(args, services: Services) -> int:
    """Refresh the price cache, incrementally or in full."""
    market = services.market
    print(f"Universe: {len(market.data.universe)} tickers. Cached prices end {market.data.as_of}.")
    if args.full:
        print("Full re-download, 2006 -> today. This takes a while and rewrites the price cache ...")
    try:
        changed = market.refresh(full=args.full)
    except DomainError:
        raise
    except Exception as err:  # pylint: disable=broad-except  # Yahoo and the network fail in many ways; none is fatal
        print(f"wolvwealth: price refresh failed ({err}). The cached prices are unchanged.", file=sys.stderr)
        return 1
    if changed:
        print(f"Done. Prices now end {market.data.as_of}.")
    else:
        print("Prices already up to date.")
    if market.tbill is not None:
        print(f"13-week T-bill yield: {market.tbill['rate']:.2%} as of {market.tbill['as_of']}.")
    for ticker, note in market.readjustments.items():
        print(f"Re-adjusted {ticker} (split or dividend detected {note['detected']}): {note['outcome']}.")
    for ticker, entry in market.quarantine.items():
        print(f"Quarantined {ticker} (excluded from allocations since {entry['detected']}): {entry['reason']}.")
    for ticker, entry in market.price_exclusions.items():
        print(
            f"{ticker} still trades, but some of its prices are unused (since {entry['detected']}): "
            f"{entry['reason']}."
        )
    if market.quarantine or market.price_exclusions:
        print("If one of those moves really happened, run `wolvwealth accept-price TICKER DATE` to keep it.")
    return 0


def cmd_accept_price(args, services: Services) -> int:
    """Accept a flagged price move as real and re-judge the ticker."""
    ticker = args.ticker.strip().upper()
    remaining = services.market.accept_price_move(ticker, args.date)
    print(f"Accepted {ticker}'s price move on {args.date}; it will not be flagged again.")
    if remaining is None:
        print(f"{ticker} now uses its full price history.")
    else:
        print(f"{ticker} is still restricted for another reason: {remaining['reason']}.")
    return 0


def cmd_mcp(args, services: Services) -> int:
    """Serve MCP over stdio, or print the client configuration."""
    from wolvwealth.mcp import server  # pylint: disable=import-outside-toplevel

    if args.config:
        print(json.dumps(server.client_config(), indent=2))
        sys.stdout.flush()  # keep the JSON above the guidance when both reach one terminal
        print(server.config_guidance(), file=sys.stderr)
        return 0
    return server.run(services)


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
    "optimize": cmd_optimize,
    "mcp": cmd_mcp,
    "refresh-data": cmd_refresh_data,
    "accept-price": cmd_accept_price,
    "reset-db": cmd_reset_db,
    "dump-db": cmd_dump_db,
}


def _log_to_stderr(message: str) -> None:
    print(message, file=sys.stderr)


def main(argv=None, services: Services = None) -> int:
    """Parse arguments and dispatch to a command."""
    args = build_parser().parse_args(argv)
    if services is None:
        # Under MCP, stdout is the protocol stream; every diagnostic (refresh
        # progress, download shortfalls) must go to stderr or it corrupts it.
        services = Services.build(log=_log_to_stderr if args.command == "mcp" else print)
    try:
        return COMMANDS[args.command](args, services)
    except DomainError as error:  # unreadable stored data, mostly; each command reports its own input errors
        return _report_error(error, getattr(args, "json", False))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
