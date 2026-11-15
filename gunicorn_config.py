"""Gunicorn production configuration.

Run with: gunicorn -c gunicorn_config.py "wolvwealth:app"
All values may be overridden via environment variables (see .env.example).
"""

import os
import sys

# Keep at 1 unless you add sticky routing: backtest jobs (/api/backtest/) are held
# in process memory, so a poll that lands on a different worker returns 404.
workers = int(os.environ.get("GUNICORN_PROCESSES", "1"))
if workers > 1:
    print(
        f"WARNING: GUNICORN_PROCESSES={workers}; backtest job polling requires requests to reach "
        "the worker that started the job (sticky routing).",
        file=sys.stderr,
    )

threads = int(os.environ.get("GUNICORN_THREADS", "4"))

# Bind an unprivileged port and serve behind a reverse proxy (nginx, Caddy, ...).
bind = os.environ.get("GUNICORN_BIND", "0.0.0.0:8000")

# Only trust X-Forwarded-* headers from these proxy IPs (comma-separated).
forwarded_allow_ips = os.environ.get("GUNICORN_FORWARDED_ALLOW_IPS", "127.0.0.1")
