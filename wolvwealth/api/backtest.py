"""POST /api/backtest/ - run the optimizer over history with benchmarks.

Thin transport layer: authentication and billing live here, everything about
the simulation lives in wolvwealth.backtest. Accepts either an API key
(Authorization header, like /api/optimize/) or a logged-in browser session
(session cookie plus X-CSRF-Token header). Either way one use is charged to
the account's token per request.
"""

import hmac

import flask

import wolvwealth
from wolvwealth.api.api_exceptions import InvalidUsage
from wolvwealth.api.auth import check_api_key, check_user_exists, consume_session_use
from wolvwealth.api.state import ApplicationState
from wolvwealth.backtest.cache import ReportCache
from wolvwealth.backtest.request import BacktestLimits, parse_backtest_request
from wolvwealth.backtest.service import run_report
from wolvwealth.optimizer_params import InvalidOptimizationInput

report_cache = ReportCache(maxsize=wolvwealth.app.config["BACKTEST_CACHE_SIZE"])


def limits_from_config() -> BacktestLimits:
    """Request limits from the Flask configuration."""
    return BacktestLimits(
        max_rebalances=wolvwealth.app.config["BACKTEST_MAX_REBALANCES"],
        max_universe_size=wolvwealth.app.config["BACKTEST_MAX_UNIVERSE_SIZE"],
    )


def authorize_and_charge() -> None:
    """Accept an API key or a logged-in session; charge one use either way."""
    if flask.request.headers.get("Authorization") is not None:
        check_api_key()
        return
    username = flask.session.get("username")
    if username is None or not check_user_exists(username):
        raise InvalidUsage("No API key provided.", status_code=401)
    expected = flask.session.get("csrf_token", "")
    submitted = flask.request.headers.get("X-CSRF-Token", "")
    if not expected or not hmac.compare_digest(expected.encode(), submitted.encode()):
        raise InvalidUsage("Authorization Error. Missing or invalid CSRF token.", status_code=403)
    consume_session_use(username)


@wolvwealth.app.route("/api/backtest/", methods=["POST"])
def backtest():
    """Validate the request, bill the caller, run (or fetch) the report."""
    payload = flask.request.get_json(silent=True)
    if payload is None:
        raise InvalidUsage("Parse Error. Unable to parse request as JSON.")
    state = ApplicationState()
    try:
        request = parse_backtest_request(
            payload,
            ticker_universe=state.TICKER_UNIVERSE,
            prices=state.HISTORICAL_PRICES,
            limits=limits_from_config(),
        )
    except InvalidOptimizationInput as err:
        raise InvalidUsage(str(err)) from err

    authorize_and_charge()  # After validation so malformed requests do not cost a use.

    try:
        report, cached = report_cache.get_or_compute(
            state.HISTORICAL_PRICES,
            request.config,
            request.benchmark_tickers,
            lambda: run_report(state.HISTORICAL_PRICES, request.config, request.benchmark_tickers),
        )
    except ValueError as err:
        wolvwealth.app.logger.exception("Backtest failed")
        raise InvalidUsage(f"Backtest Error. {err}") from err
    body = report.to_dict(include_equity=request.include_equity)
    body["cached"] = cached
    return flask.jsonify(body)
