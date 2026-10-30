"""Settings: path fields are usable however they were supplied."""
import pathlib

from wolvwealth.config import Settings


def test_string_paths_are_coerced_so_joins_work():
    """Regression: a str var_dir was accepted, then blew up in an unrelated property."""
    settings = Settings(var_dir="/tmp/scratch")
    assert isinstance(settings.var_dir, pathlib.Path)
    assert settings.prices_path == pathlib.Path("/tmp/scratch/historical_prices.parquet")
    assert settings.database_path == pathlib.Path("/tmp/scratch/wolvwealth.sqlite3")
    assert settings.secret_key_path == pathlib.Path("/tmp/scratch/secret_key")
    assert settings.legacy_csv_paths[0] == pathlib.Path("/tmp/scratch/historical_prices.csv")


def test_string_ticker_universe_path_is_coerced():
    assert isinstance(Settings(ticker_universe_path="/tmp/t.csv").ticker_universe_path, pathlib.Path)


def test_paths_given_as_paths_are_left_alone(tmp_path):
    settings = Settings(var_dir=tmp_path)
    assert settings.var_dir is tmp_path


def test_defaults_are_paths():
    settings = Settings()
    assert isinstance(settings.var_dir, pathlib.Path)
    assert isinstance(settings.ticker_universe_path, pathlib.Path)


def test_from_env_reads_the_overrides():
    settings = Settings.from_env({"WOLVWEALTH_VAR": "/tmp/env", "WOLVWEALTH_NO_AUTO_REFRESH": "1"})
    assert settings.var_dir == pathlib.Path("/tmp/env")
    assert settings.auto_refresh is False
    assert Settings.from_env({}).auto_refresh is True
