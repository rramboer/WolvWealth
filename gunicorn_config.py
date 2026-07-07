"""Gunicorn production configuration.

Run with: gunicorn -c gunicorn_config.py "wolvwealth:app"
All values may be overridden via environment variables (see .env.example).
"""

import os

workers = int(os.environ.get("GUNICORN_PROCESSES", "1"))

threads = int(os.environ.get("GUNICORN_THREADS", "4"))

# Bind an unprivileged port and serve behind a reverse proxy (nginx, Caddy, ...).
bind = os.environ.get("GUNICORN_BIND", "0.0.0.0:8000")

# Only trust X-Forwarded-* headers from these proxy IPs (comma-separated).
forwarded_allow_ips = os.environ.get("GUNICORN_FORWARDED_ALLOW_IPS", "127.0.0.1")
