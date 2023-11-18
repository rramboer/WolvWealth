"""API Portfolio Optimization."""
import flask
# import pypfopt
import wolvwealth


@wolvwealth.app.route('/api/optimize/')
def optimize():
    """Optimization Endpoint."""
    context = {
        "optimization": "**TODO**"
    }
    return flask.jsonify(**context)
