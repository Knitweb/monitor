"""The graph layout is the expensive part of build_graph (O(n^2)x120 in the
pure-Python fallback) and runs on every /api/graph poll. It is deterministic,
so identical topology is memoized. These tests pin that the cache is correct
(same result as a fresh compute) and actually hits on a repeated topology."""

from knitweb_monitor import _layout, _layout_compute, _layout_cache


def setup_function():
    _layout_cache.clear()


def test_layout_cache_matches_fresh_compute_and_is_deterministic():
    nodes = [f"n{i}" for i in range(10)]
    edges = [("n0", "n1"), ("n1", "n2"), ("n2", "n3"), ("n4", "n5")]

    fresh1 = _layout_compute(nodes, edges)
    fresh2 = _layout_compute(nodes, edges)
    assert fresh1 == fresh2, "layout must be deterministic"

    cached = _layout(nodes, edges)
    assert cached == fresh1, "cached layout must equal a fresh compute"


def test_repeated_topology_hits_the_cache():
    nodes = ["a", "b", "c"]
    edges = [("a", "b")]

    first = _layout(nodes, edges)
    assert (tuple(nodes), tuple(edges)) in _layout_cache
    # A second call with identical topology returns the very same object.
    assert _layout(nodes, edges) is first


def test_changed_topology_is_a_distinct_cache_entry():
    _layout(["a", "b"], [("a", "b")])
    _layout(["a", "b", "c"], [("a", "b")])
    assert len(_layout_cache) == 2


def test_empty_graph_is_handled():
    assert _layout([], []) == {}


def test_layout_falls_back_when_optional_networkx_backend_fails(monkeypatch):
    import knitweb_monitor as km

    class BrokenGraph:
        def add_nodes_from(self, nodes):
            pass

        def add_edges_from(self, edges):
            pass

    class BrokenNetworkx:
        DiGraph = BrokenGraph

        @staticmethod
        def spring_layout(*args, **kwargs):
            raise ModuleNotFoundError("No module named 'numpy'")

    monkeypatch.setattr(km, "_nx", BrokenNetworkx)

    layout = km._layout_compute(["a", "b"], [("a", "b")])

    assert set(layout) == {"a", "b"}


def test_cache_is_bounded_and_does_not_grow_unbounded():
    from knitweb_monitor import _layout, _layout_cache, _LAYOUT_CACHE_MAX
    _layout_cache.clear()
    # Feed many DISTINCT topologies (more than the cap) — the cache must stay bounded.
    for i in range(_LAYOUT_CACHE_MAX * 2 + 5):
        _layout([f"n{i}a", f"n{i}b"], [(f"n{i}a", f"n{i}b")])
        assert len(_layout_cache) <= _LAYOUT_CACHE_MAX, "layout cache must never exceed its bound"
    # And it still serves correctly after eviction churn.
    nodes, edges = ["x", "y"], [("x", "y")]
    assert _layout(nodes, edges) == _layout(nodes, edges)
