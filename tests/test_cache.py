"""Unit tests for the bounded LRU memo cache."""

from wolvwealth.domain.cache import MISS, LRUCache


def test_get_and_put_round_trip_and_count_hits_and_misses():
    cache = LRUCache()
    assert cache.get("absent") is MISS
    assert cache.misses == 1 and cache.hits == 0
    cache.put("k", {"AAA": 1.0})
    assert cache.get("k") == {"AAA": 1.0}
    assert cache.hits == 1 and len(cache) == 1
    cache.put("none", None)
    assert cache.get("none") is None and cache.hits == 2, "None is a cachable value, distinct from MISS"


def test_evicts_the_least_recently_used_entry_and_a_read_counts_as_use():
    cache = LRUCache(maxsize=2)
    cache.put("a", 1)
    cache.put("b", 2)
    cache.put("c", 3)
    assert len(cache) == 2
    assert cache.get("a") is MISS
    assert cache.get("b") == 2 and cache.get("c") == 3
    cache.get("b")  # "b" is now the most recently used, so "c" is next out
    cache.put("d", 4)
    assert cache.get("b") == 2 and cache.get("c") is MISS


def test_get_or_compute_runs_the_function_once_per_key_and_forgets_failures():
    cache = LRUCache()
    calls = []

    def compute():
        calls.append(1)
        return "value"

    def boom():
        raise RuntimeError("solver blew up")

    assert cache.get_or_compute("k", compute) == "value"
    assert cache.get_or_compute("k", compute) == "value"
    assert len(calls) == 1
    for _ in range(2):
        try:
            cache.get_or_compute("failed", boom)
        except RuntimeError:
            pass
    assert len(cache) == 1 and cache.get("failed") is MISS
