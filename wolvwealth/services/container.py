"""Services: one object holding everything the adapters (web, CLI) need.

Build it once per process with ``Services.build()``. Tests build one against
a temp directory and hand it to ``create_app``.
"""

import dataclasses

from wolvwealth.config import Settings
from wolvwealth.market import stooq, yahoo
from wolvwealth.services.market_data import MarketDataService
from wolvwealth.storage.database import Database
from wolvwealth.storage.price_store import PriceStore
from wolvwealth.storage.repository import PortfolioRepository


@dataclasses.dataclass
class Services:
    """Wired collaborators for one install."""

    settings: Settings
    database: Database
    price_store: PriceStore
    market: MarketDataService

    @classmethod
    def build(cls, settings: Settings = None, log=print) -> "Services":
        """Wire the default implementations from settings (env-derived by default)."""
        settings = settings or Settings.from_env()
        store = PriceStore(settings.prices_path, settings.ticker_universe_path, settings.legacy_csv_paths)
        return cls(
            settings=settings,
            database=Database(settings.database_path),
            price_store=store,
            market=MarketDataService(
                store,
                settings.price_history_start,
                log=log,
                stats_universe_size=settings.stats_universe_size,
                # The second opinions a flagged price move gets before it
                # excludes anything. Wired here, not defaulted in the
                # service, so tests and ad-hoc constructions stay offline.
                verifier=stooq.download_closes,
                splits_source=yahoo.download_splits,
            ),
        )

    @staticmethod
    def repository(connection) -> PortfolioRepository:
        """Return a repository bound to an open connection."""
        return PortfolioRepository(connection)
