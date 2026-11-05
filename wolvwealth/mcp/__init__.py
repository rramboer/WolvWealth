"""MCP server: the app's use cases as self-describing tools over stdio, for AI apps.

An AI app (Claude Desktop, Claude Code, Cursor, ...) starts ``wolvwealth mcp``
as a child process and speaks JSON-RPC to it over stdin/stdout. No port, no
URL, nothing to keep running; only the app that started the process can talk
to it. The tools call the same ``services/`` functions as the JSON API and
the CLI, so the three cannot disagree.

Hand-rolled on purpose: the server needs the handshake, ``tools/list`` and
``tools/call`` and nothing else, which is a few hundred lines of plain
JSON-RPC. That keeps the install dependency-free (an optional extra is a
step people miss, and a required SDK lands on every machine) and immune to
the official SDK's major-version churn. ``protocol.py`` is the transport,
``tools.py`` the tools, ``server.py`` the MCP methods on top.
"""
