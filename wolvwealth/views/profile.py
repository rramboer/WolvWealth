"""Routes for the profile page."""

from flask import render_template
import wolvwealth

@wolvwealth.app.route('/profile/', methods=['GET'])
def show_profile():
    """Display /profile route."""
    context = {}
    return render_template("profile.html", **context)
