"""The suite's network guard (conftest ``no_network``) actually catches the ways out."""

import socket
import urllib.request

import pytest
from _pytest.outcomes import Failed

from wolvwealth.market import yahoo


def test_a_real_price_download_fails_the_test_even_through_except_exception():
    with pytest.raises(Failed, match="yfinance.download"):
        try:
            yahoo.download_prices(["AAPL"], "2024-01-01", "2024-01-05", sleep=lambda _: None)
        except Exception:  # pylint: disable=broad-except  # the app's own fallbacks look like this
            pass


def test_plain_python_http_is_refused():
    with pytest.raises(Failed, match="DNS lookup"):
        urllib.request.urlopen("https://query1.finance.yahoo.com/", timeout=1)  # pylint: disable=consider-using-with
    with pytest.raises(Failed, match="socket.connect"):
        with socket.socket() as sock:
            sock.connect(("8.8.8.8", 443))


def test_loopback_is_still_allowed():
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        with socket.create_connection(server.getsockname(), timeout=1):
            pass
