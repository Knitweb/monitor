"""http_json short-TTL cache: a single poll tick and concurrent browser polls share ONE blocking
fetch per URL instead of fanning out duplicates (read_molgang + _game_links both hit /api/web)."""

import io
import knitweb_monitor as km


class _FakeResp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): self.close()


def _patch(monkeypatch, payload=b'{"ok": true}'):
    calls = {"n": 0}

    def fake_urlopen(url, timeout=2.5):
        calls["n"] += 1
        return _FakeResp(payload)

    monkeypatch.setattr(km.urllib.request, "urlopen", fake_urlopen)
    return calls


def _clock(monkeypatch, t):
    monkeypatch.setattr(km.time, "monotonic", lambda: t[0])


def test_same_url_within_ttl_fetches_once(monkeypatch):
    km._HTTP_CACHE.clear()
    t = [1000.0]
    _clock(monkeypatch, t)
    calls = _patch(monkeypatch)
    a = km.http_json("http://x/api/web")
    b = km.http_json("http://x/api/web")   # within TTL → cached
    assert a == b == {"ok": True}
    assert calls["n"] == 1


def test_distinct_urls_each_fetch(monkeypatch):
    km._HTTP_CACHE.clear()
    t = [1000.0]
    _clock(monkeypatch, t)
    calls = _patch(monkeypatch)
    km.http_json("http://x/api/web")
    km.http_json("http://x/api/state")
    assert calls["n"] == 2


def test_cache_expires_after_ttl(monkeypatch):
    km._HTTP_CACHE.clear()
    t = [1000.0]
    _clock(monkeypatch, t)
    calls = _patch(monkeypatch)
    km.http_json("http://x/api/web")
    t[0] += km._HTTP_CACHE_TTL + 0.1       # advance past TTL
    km.http_json("http://x/api/web")       # refetched
    assert calls["n"] == 2
