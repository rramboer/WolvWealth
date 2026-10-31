"""API.md is the contract agents are handed; keep it honest about the real app.

These check the things that silently rot: an endpoint renamed, a request
field documented but not parsed, a response key documented but never sent.
"""

import pathlib
import re

import pytest

CONTRACT = pathlib.Path(__file__).resolve().parent.parent / "API.md"
TEXT = CONTRACT.read_text(encoding="utf-8")
#: Prose only: fenced examples are stripped so their ``` delimiters cannot be
#: mistaken for inline-code backticks.
PROSE = re.sub(r"```.*?```", "", TEXT, flags=re.DOTALL)
#: Every name API.md marks up as code, nested inputs reduced to their leaf
#: (`constraints.max_weight` -> `max_weight`).
DOCUMENTED = {token.split(".")[-1] for token in re.findall(r"`([^`\n]+)`", PROSE)}


def documented_paths() -> set:
    """Every /api/... path API.md mentions."""
    return set(re.findall(r"`?(/api/[a-z/]*)`?", TEXT))


def test_every_documented_endpoint_exists(app):
    routes = {str(rule) for rule in app.url_map.iter_rules()}
    missing = documented_paths() - routes
    assert not missing, f"API.md documents routes the app does not serve: {sorted(missing)}"


def test_every_api_endpoint_is_documented(app):
    served = {str(rule) for rule in app.url_map.iter_rules() if str(rule).startswith("/api/")}
    undocumented = served - documented_paths()
    assert not undocumented, f"endpoints missing from API.md: {sorted(undocumented)}"


@pytest.mark.parametrize(
    "field",
    [
        "initial_cash",
        "initial_holdings",
        "universe",
        "max_weight",
        "max_positions",
        "min_universal_weight",
        "max_turnover",
        "risk_free_rate",
        "exclude_metrics",
        "exclude_trades",
    ],
)
def test_documented_request_fields_are_actually_parsed(field):
    """Every documented optimize input must appear in the parser."""
    source = (pathlib.Path(__file__).resolve().parent.parent / "wolvwealth" / "domain" / "optimizer.py").read_text(
        encoding="utf-8"
    )
    assert f'"{field}"' in source, f"the parser must read every documented field: {field}"
    assert field in DOCUMENTED, f"API.md never documents {field}"


def test_documented_response_keys_are_the_ones_sent(client):
    body = client.post("/api/optimize/", json={"initial_cash": 1000, "universe": ["top20"]}).get_json()
    for key in ("as_of", "optimized_portfolio", "trades", "metrics"):
        assert key in body and key in DOCUMENTED
    for key in ("shares", "value", "percent_weight"):
        assert key in next(iter(body["optimized_portfolio"].values()))
    for key in ("ticker", "action", "shares", "price", "value"):
        assert key in body["trades"][0]
    for key in (
        "portfolio_value",
        "expected_annual_return",
        "annual_volatility",
        "sharpe_ratio",
        "num_assets",
        "turnover",
        "risk_free_rate",
    ):
        assert key in body["metrics"] and key in DOCUMENTED


def test_documented_error_shape_matches(client):
    body = client.post("/api/optimize/", json={"initial_cash": 100, "universe": ["FAKETICKER"]}).get_json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"message", "status_code"}
    assert body["error"]["message"] in TEXT  # the sample error is quoted verbatim
