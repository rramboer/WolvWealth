"""Domain layer: portfolio maths and rules. Pure Python plus numeric libraries.

Nothing in this package imports Flask, SQLite, the filesystem, or the network.
The outer layers (storage, market, services, web, CLI) depend on this one;
it depends on none of them.
"""
