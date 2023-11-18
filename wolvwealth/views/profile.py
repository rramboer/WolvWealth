"""Routes for the profile page."""

import wolvwealth
from flask import render_template

@wolvwealth.app.route('/profile/', methods=['GET'])
def show_profile():
    """Display /profile route."""
    context = {}
    return render_template("profile.html", **context)