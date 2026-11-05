"""Enforce the dependency rule: each layer imports only from layers inside it.

domain            -> (nothing in wolvwealth outside domain)
storage, market   -> domain
services          -> domain, storage, market, config
web, mcp, __main__ -> everything above
"""

import ast
import pathlib

import pytest

PACKAGE = pathlib.Path(__file__).resolve().parent.parent / "wolvwealth"

ALLOWED = {
    "domain": {"domain"},
    "storage": {"storage", "domain"},
    "market": {"market", "domain"},
    "services": {"services", "domain", "storage", "market", "config"},
    "web": {"web", "services", "domain", "storage", "market", "config"},
    "mcp": {"mcp", "services", "domain", "storage", "market", "config"},
    "config": set(),
    "__main__": {"services", "web", "mcp", "config", "domain"},
    "__init__": {"web"},
}


def _imports(path: pathlib.Path):
    tree = ast.parse(path.read_text(), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module


def _layer(path: pathlib.Path) -> str:
    relative = path.relative_to(PACKAGE)
    return relative.parts[0] if len(relative.parts) > 1 else relative.stem


@pytest.mark.parametrize("path", sorted(PACKAGE.rglob("*.py")), ids=lambda p: str(p.relative_to(PACKAGE)))
def test_layer_imports_only_inner_layers(path):
    layer = _layer(path)
    assert layer in ALLOWED, f"new top-level module {layer}: add it to the layer map"
    for module in _imports(path):
        if not module.startswith("wolvwealth"):
            continue
        parts = module.split(".")
        target = parts[1] if len(parts) > 1 else None
        if target is None:  # bare `import wolvwealth`
            target = "__init__"
        assert target in ALLOWED[layer], f"{path.relative_to(PACKAGE)} ({layer}) must not import {module}"


def test_domain_is_framework_free():
    forbidden = {"flask", "sqlite3", "yfinance", "requests"}
    for path in (PACKAGE / "domain").rglob("*.py"):
        for module in _imports(path):
            assert module.split(".")[0] not in forbidden, f"{path.name} imports {module}"
