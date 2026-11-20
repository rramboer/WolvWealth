"""Route and code for optimizer page.

GET /optimizer/"""

import flask

import wolvwealth
from wolvwealth.api.state import ApplicationState
from wolvwealth.views.accounts import is_logged_in


@wolvwealth.app.route("/optimizer/", methods=["GET"])
def show_optimizer():
    """Display /optimizer route.

    Facts the React app needs are rendered as data attributes on its mount
    point: the CSRF token for session-authenticated API calls, the price data
    range for sensible date defaults, and the account's remaining uses.
    """
    if is_logged_in() is False:
        return flask.redirect(flask.url_for("show_login"))
    state = ApplicationState()
    prices = state.HISTORICAL_PRICES
    connection = wolvwealth.model.get_db()
    token = connection.execute("SELECT uses FROM tokens WHERE owner = ?", (flask.session["username"],)).fetchone()
    context = {
        "user": {"is_authenticated": True},
        "app_data": {
            "first_price_date": prices.index[0].date().isoformat(),
            "last_price_date": prices.index[-1].date().isoformat(),
            "universe_size": len(state.TICKER_UNIVERSE),
            "uses_remaining": token["uses"] if token else 0,
            "max_rebalances": wolvwealth.app.config["BACKTEST_MAX_REBALANCES"],
            "max_universe_size": wolvwealth.app.config["BACKTEST_MAX_UNIVERSE_SIZE"],
        },
    }
    return flask.render_template("optimizer.html", **context)
