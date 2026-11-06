"""Settings: path fields are usable however they were supplied."""

import pathlib

from wolvwealth.config import Settings


def test_path_fields_are_paths_however_supplied(tmp_path):
    """Regression: a str var_dir was accepted, then blew up in an unrelated property."""
    settings = Settings(var_dir="/tmp/scratch", ticker_universe_path="/tmp/t.csv")
    assert isinstance(settings.var_dir, pathlib.Path) and isinstance(settings.ticker_universe_path, pathlib.Path)
    assert settings.prices_path == pathlib.Path("/tmp/scratch/historical_prices.parquet")
    assert settings.database_path == pathlib.Path("/tmp/scratch/wolvwealth.sqlite3")
    assert settings.secret_key_path == pathlib.Path("/tmp/scratch/secret_key")
    assert settings.legacy_csv_paths[0] == pathlib.Path("/tmp/scratch/historical_prices.csv")
    assert Settings(var_dir=tmp_path).var_dir is tmp_path, "a Path is left alone"
    defaults = Settings()
    assert isinstance(defaults.var_dir, pathlib.Path) and isinstance(defaults.ticker_universe_path, pathlib.Path)


def test_from_env_reads_the_overrides():
    settings = Settings.from_env({"WOLVWEALTH_VAR": "/tmp/env", "WOLVWEALTH_NO_AUTO_REFRESH": "1"})
    assert settings.var_dir == pathlib.Path("/tmp/env")
    assert settings.auto_refresh is False
    assert Settings.from_env({}).auto_refresh is True
