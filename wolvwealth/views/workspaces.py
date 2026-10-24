"""Workspace selection and lifecycle.

A workspace is a person using this install (no passwords, no accounts).
The active workspace name lives in the session cookie; every page that
shows portfolio data requires one to be open.
"""

import flask
import wolvwealth
import wolvwealth.portfolios as portfolios


def current_workspace():
    """Return the active workspace name, or None.

    Drops the session value if the workspace was deleted since it was opened.
    """
    name = flask.session.get("workspace")
    if name is None:
        return None
    if not portfolios.workspace_exists(name):
        flask.session.pop("workspace", None)
        return None
    return name


@wolvwealth.app.route("/workspaces/", methods=["GET"])
def show_workspaces():
    """Display the workspace picker."""
    context = {
        "workspaces": portfolios.fetch_workspaces(),
        "workspace": current_workspace(),
    }
    return flask.render_template("workspaces.html", **context)


@wolvwealth.app.route("/workspaces/create/", methods=["POST"])
def workspaces_create():
    """Create a workspace and open it."""
    name = (flask.request.form.get("name") or "").strip()
    if not portfolios.valid_name(name):
        flask.flash("Workspace names are 1-64 letters, digits, spaces, dots, dashes, or underscores.")
        return flask.redirect(flask.url_for("show_workspaces"))
    if portfolios.workspace_exists(name):
        flask.flash(f"Workspace '{name}' already exists.")
        return flask.redirect(flask.url_for("show_workspaces"))
    portfolios.create_workspace(name)
    flask.session["workspace"] = name
    return flask.redirect(flask.url_for("show_workspace"))


@wolvwealth.app.route("/workspaces/enter/", methods=["POST"])
def workspaces_enter():
    """Open an existing workspace."""
    name = flask.request.form.get("name")
    if name is None or not portfolios.workspace_exists(name):
        flask.flash("That workspace no longer exists.")
        return flask.redirect(flask.url_for("show_workspaces"))
    flask.session["workspace"] = name
    return flask.redirect(flask.url_for("show_workspace"))


@wolvwealth.app.route("/workspaces/leave/", methods=["GET", "POST"])
def workspaces_leave():
    """Close the active workspace (the old 'logout')."""
    flask.session.pop("workspace", None)
    return flask.redirect(flask.url_for("show_workspaces"))


@wolvwealth.app.route("/workspaces/delete/", methods=["POST"])
def workspaces_delete():
    """Delete a workspace and everything in it."""
    name = flask.request.form.get("name")
    if name is not None and portfolios.workspace_exists(name):
        portfolios.delete_workspace(name)
        flask.flash(f"Workspace '{name}' deleted.")
    if flask.session.get("workspace") == name:
        flask.session.pop("workspace", None)
    return flask.redirect(flask.url_for("show_workspaces"))


# Old bookmarks from the account/login era land on the equivalent page.
@wolvwealth.app.route("/login/", methods=["GET"])
@wolvwealth.app.route("/register/", methods=["GET"])
def legacy_login_redirect():
    """Redirect the removed login/register pages to the workspace picker."""
    return flask.redirect(flask.url_for("show_workspaces"))


@wolvwealth.app.route("/account/", methods=["GET"])
def legacy_account_redirect():
    """Redirect the removed account page to the workspace page."""
    return flask.redirect(flask.url_for("show_workspace"))
