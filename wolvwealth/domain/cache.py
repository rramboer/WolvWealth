"""A small bounded LRU memo cache for expensive derived results.

Used for the max-Sharpe solve, which is ~90% of an optimize request and is a
pure function of the prices and a few request fields. A cache instance lives
on the ``MarketData`` snapshot its entries were computed from, so a price
refresh builds a new snapshot and drops every entry with it. That is the
point of the design: there is no expiry or invalidation logic to get wrong,
and no way to serve a portfolio computed from prices that are no longer
current.
"""

import collections
import threading

#: Sentinel returned by ``get`` for an absent key, so that None can be cached.
MISS = object()

#: Entries per cache. An optimize request stores at most two (its weights and
#: its metrics), each a dict of a few hundred floats or a pair of them, so a
#: full cache is megabytes at worst.
DEFAULT_MAXSIZE = 128


class LRUCache:
    """Thread-safe bounded cache; the least recently used entry is evicted first."""

    def __init__(self, maxsize: int = DEFAULT_MAXSIZE) -> None:
        """Create an empty cache holding at most ``maxsize`` entries."""
        self.maxsize = maxsize
        self.hits = 0
        self.misses = 0
        self._entries = collections.OrderedDict()
        self._lock = threading.Lock()

    def __len__(self) -> int:
        """Return the number of entries currently held."""
        return len(self._entries)

    def get(self, key):
        """Return the value stored for ``key``, or ``MISS``, marking it most recently used."""
        with self._lock:
            if key not in self._entries:
                self.misses += 1
                return MISS
            self._entries.move_to_end(key)
            self.hits += 1
            return self._entries[key]

    def put(self, key, value) -> None:
        """Store ``value`` under ``key``, evicting the oldest entries past ``maxsize``."""
        with self._lock:
            self._entries[key] = value
            self._entries.move_to_end(key)
            while len(self._entries) > self.maxsize:
                self._entries.popitem(last=False)

    def get_or_compute(self, key, compute):
        """Return the cached value for ``key``, calling ``compute()`` on a miss.

        ``compute`` runs outside the lock so a slow solve never blocks other
        callers. Two threads missing the same key therefore both compute it;
        the result is a pure function of the key, so the duplicate work is
        wasted but the stored value is the same either way.
        """
        value = self.get(key)
        if value is MISS:
            value = compute()
            self.put(key, value)
        return value
