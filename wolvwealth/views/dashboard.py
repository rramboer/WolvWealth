"""Route and code for dashboard page.

GET /dashboard/"""

from flask import render_template
import wolvwealth

@wolvwealth.app.route('/dashboard/', methods=['GET'])
def show_dashboard():
    """Display /dashboard route."""
    context = {}
    return render_template("dashboard.html", **context)
