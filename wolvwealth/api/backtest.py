"""Backtest endpoints: start a job, then poll it for progress and the report.

    POST /api/backtest/           validate, charge one use, start a job  -> 202 (200 on a cache hit)
    GET  /api/backtest/<job_id>/  status, progress, ETA, and the report when done

Thin transport layer: authentication and billing live here; validation,
caching, job execution and the simulation live in wolvwealth.backtest.
Accepts either an API key or a logged-in browser session (see
wolvwealth.api.auth.authenticate). Starting a job from a session requires an
X-CSRF-Token header; polling does not, because it changes nothing.
"""

import flask

import wolvwealth
from wolvwealth.api.api_exceptions import InvalidUsage
from wolvwealth.api.auth import authenticate
from wolvwealth.api.state import ApplicationState
from wolvwealth.backtest.cache import ReportCache
from wolvwealth.backtest.jobs import JobLimitError, JobManager
from wolvwealth.backtest.request import BacktestLimits, parse_backtest_request
from wolvwealth.backtest.service import run_report
from wolvwealth.optimizer_params import InvalidOptimizationInput

_config = wolvwealth.app.config
report_cache = ReportCache(maxsize=_config["BACKTEST_CACHE_SIZE"])
job_manager = JobManager(
    max_workers=_config["BACKTEST_MAX_WORKERS"],
    max_pending=_config["BACKTEST_MAX_PENDING_JOBS"],
    max_active_per_owner=_config["BACKTEST_MAX_JOBS_PER_USER"],
    ttl_seconds=_config["BACKTEST_JOB_TTL_SECONDS"],
)


def limits_from_config() -> BacktestLimits:
    """Request limits from the Flask configuration."""
    return BacktestLimits(
        max_rebalances=_config["BACKTEST_MAX_REBALANCES"],
        max_universe_size=_config["BACKTEST_MAX_UNIVERSE_SIZE"],
    )


def job_response(snapshot: dict) -> flask.Response:
    """Serialize a job snapshot with its polling URL."""
    snapshot["status_url"] = flask.url_for("backtest_status", job_id=snapshot["job_id"])
    response = flask.jsonify(snapshot)
    if snapshot["status"] in ("queued", "running"):
        response.status_code = 202
        response.headers["Location"] = snapshot["status_url"]
    return response


@wolvwealth.app.route("/api/backtest/", methods=["POST"])
def backtest_start():
    """Validate the request, then start a job (or finish one instantly from cache)."""
    payload = flask.request.get_json(silent=True)
    if payload is None:
        raise InvalidUsage("Parse Error. Unable to parse request as JSON.")
    state = ApplicationState()
    prices = state.HISTORICAL_PRICES
    try:
        request = parse_backtest_request(
            payload, ticker_universe=state.TICKER_UNIVERSE, prices=prices, limits=limits_from_config()
        )
    except InvalidOptimizationInput as err:
        raise InvalidUsage(str(err)) from err

    # Validation happens first so malformed requests cost nothing; the charge
    # runs inside the job manager's capacity check so refused jobs cost nothing.
    caller = authenticate(require_csrf=True)
    config, benchmarks = request.config, request.benchmark_tickers
    try:
        cached_report = report_cache.peek(prices, config, benchmarks)
        if cached_report is not None:
            snapshot = job_manager.complete(
                caller.owner, cached_report, include_equity=request.include_equity, on_accept=caller.charge
            )
        else:

            def work(on_progress):
                report, _ = report_cache.get_or_compute(
                    prices, config, benchmarks, lambda: run_report(prices, config, benchmarks, on_progress)
                )
                return report

            snapshot = job_manager.submit(
                caller.owner, work, include_equity=request.include_equity, on_accept=caller.charge
            )
    except JobLimitError as err:
        raise InvalidUsage(str(err), status_code=429 if err.scope == "owner" else 503) from err
    return job_response(snapshot)


@wolvwealth.app.route("/api/backtest/<job_id>/", methods=["GET"])
def backtest_status(job_id: str):
    """Report a job's progress, and its result once finished. Free; never charges."""
    caller = authenticate(require_csrf=False)
    snapshot = job_manager.get(job_id, caller.owner)
    if snapshot is None:
        raise InvalidUsage("Backtest job not found. It may have expired.", status_code=404)
    return job_response(snapshot)
