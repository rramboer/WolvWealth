"""Route and code for landing (index) page.

GET /
"""

import wolvwealth
from flask import render_template

@wolvwealth.app.route('/', methods=['GET'])
def show_landing():
    """Display / route."""
    context = {}
    return render_template("landing.html", **context)
