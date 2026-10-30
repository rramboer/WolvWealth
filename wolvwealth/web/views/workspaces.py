"""Workspace picker and lifecycle. The session cookie holds the open workspace's name."""

import flask

from wolvwealth.domain.models import NAME_RULE, valid_name
from wolvwealth.web import context
from wolvwealth.web.views import blueprint


@blueprint.route("/workspaces/", methods=["GET"])
def show_workspaces():
    """Display the picker."""
    return flask.render_template(
        "workspaces.html", workspaces=context.repo().list_workspaces(), workspace=context.current_workspace()
    )


@blueprint.route("/workspaces/create/", methods=["POST"])
def workspaces_create():
    """Create a workspace and open it."""
    name = (flask.request.form.get("name") or "").strip()
    repo = context.repo()
    if not valid_name(name):
        flask.flash(f"Workspace names are {NAME_RULE}.")
        return flask.redirect(flask.url_for("views.show_workspaces"))
    if repo.workspace_exists(name):
        flask.flash(f"Workspace '{name}' already exists.")
        return flask.redirect(flask.url_for("views.show_workspaces"))
    repo.create_workspace(name)
    flask.session["workspace"] = name
    return flask.redirect(flask.url_for("views.show_workspace"))


@blueprint.route("/workspaces/enter/", methods=["POST"])
def workspaces_enter():
    """Open an existing workspace."""
    name = flask.request.form.get("name")
    if name is None or not context.repo().workspace_exists(name):
        flask.flash("That workspace no longer exists.")
        return flask.redirect(flask.url_for("views.show_workspaces"))
    flask.session["workspace"] = name
    return flask.redirect(flask.url_for("views.show_workspace"))


@blueprint.route("/workspaces/leave/", methods=["POST"])
def workspaces_leave():
    """Close the open workspace. POST-only: no GET in this app mutates anything."""
    flask.session.pop("workspace", None)
    return flask.redirect(flask.url_for("views.show_workspaces"))


@blueprint.route("/workspaces/delete/", methods=["POST"])
def workspaces_delete():
    """Delete a workspace and everything in it."""
    name = flask.request.form.get("name")
    repo = context.repo()
    if name is not None and repo.workspace_exists(name):
        repo.delete_workspace(name)
        flask.flash(f"Workspace '{name}' deleted.")
    if flask.session.get("workspace") == name:
        flask.session.pop("workspace", None)
    return flask.redirect(flask.url_for("views.show_workspaces"))


# Old bookmarks from the account/login era land on the equivalent page.
@blueprint.route("/login/", methods=["GET"])
@blueprint.route("/register/", methods=["GET"])
def legacy_login_redirect():
    """Send the removed login/register pages to the picker."""
    return flask.redirect(flask.url_for("views.show_workspaces"))


@blueprint.route("/account/", methods=["GET"])
def legacy_account_redirect():
    """Send the removed account page to the workspace page."""
    return flask.redirect(flask.url_for("views.show_workspace"))
