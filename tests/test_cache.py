"""Unit tests for the bounded LRU memo cache."""
from wolvwealth.domain.cache import MISS, LRUCache


def test_missing_key_returns_the_sentinel():
    cache = LRUCache()
    assert cache.get("absent") is MISS
    assert cache.misses == 1 and cache.hits == 0


def test_put_then_get_round_trip_and_counts_a_hit():
    cache = LRUCache()
    cache.put("k", {"AAA": 1.0})
    assert cache.get("k") == {"AAA": 1.0}
    assert cache.hits == 1 and len(cache) == 1


def test_none_is_a_cachable_value():
    cache = LRUCache()
    cache.put("k", None)
    assert cache.get("k") is None and cache.hits == 1


def test_evicts_the_least_recently_used_entry_past_maxsize():
    cache = LRUCache(maxsize=2)
    cache.put("a", 1)
    cache.put("b", 2)
    cache.put("c", 3)
    assert len(cache) == 2
    assert cache.get("a") is MISS
    assert cache.get("b") == 2 and cache.get("c") == 3


def test_reading_an_entry_protects_it_from_the_next_eviction():
    cache = LRUCache(maxsize=2)
    cache.put("a", 1)
    cache.put("b", 2)
    cache.get("a")  # "a" is now the most recently used, so "b" is next out
    cache.put("c", 3)
    assert cache.get("a") == 1 and cache.get("b") is MISS


def test_get_or_compute_runs_the_function_once_per_key():
    cache = LRUCache()
    calls = []

    def compute():
        calls.append(1)
        return "value"

    assert cache.get_or_compute("k", compute) == "value"
    assert cache.get_or_compute("k", compute) == "value"
    assert len(calls) == 1


def test_get_or_compute_does_not_store_a_failed_computation():
    cache = LRUCache()

    def boom():
        raise RuntimeError("solver blew up")

    for _ in range(2):
        try:
            cache.get_or_compute("k", boom)
        except RuntimeError:
            pass
    assert len(cache) == 0 and cache.get("k") is MISS
