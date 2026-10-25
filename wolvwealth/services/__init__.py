"""Services layer: use cases that wire domain rules to storage and market data.

Depends on domain, storage, and market. Knows nothing about Flask or the CLI;
those are thin adapters over the functions here. A bolt-on feature normally
lands as a new module in this package plus a blueprint or CLI command.
"""
