"""WolvWealth: a local-first portfolio optimizer.

Importing this package has no side effects. ``create_app()`` builds the Flask
app (so ``flask --app wolvwealth run`` works) and ``wolvwealth serve`` is the
everyday way to run it. Layers, innermost first: domain -> storage, market ->
services -> web, CLI. Each depends only on the layers inside it.
"""

from wolvwealth.web import create_app

__all__ = ["create_app"]
