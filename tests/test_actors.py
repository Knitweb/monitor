import knitweb_monitor as km


def setup_function():
    km.CFG.github_org = "knitweb"
    km.CFG.github_token = None
    with km._ACTORS["lock"]:
        km._ACTORS["at"] = 0.0
        km._ACTORS["data"] = None
        km._ACTORS["error"] = None
        km._ACTORS["fetching"] = False


def test_collect_actors_aggregates_commits_prs_and_comments(monkeypatch):
    def fake_gh_api(path, timeout=6.0):
        if path == "/orgs/knitweb/repos?per_page=100&type=public&sort=full_name":
            return ([{"name": "repo1", "archived": False}, {"name": "archived", "archived": True}], None)
        if path == "/repos/knitweb/repo1/contributors?per_page=100&anon=0":
            return ([{"login": "alice", "contributions": 3},
                     {"login": "github-actions[bot]", "contributions": 2}], None)
        if path == "/repos/knitweb/repo1/pulls?state=all&per_page=100":
            return ([{"user": {"login": "bob"}, "merged_at": "2026-01-01T00:00:00Z"},
                     {"user": {"login": "alice"}, "merged_at": None}], None)
        if path == "/repos/knitweb/repo1/issues/comments?per_page=100&sort=created&direction=desc":
            return ([{"user": {"login": "carol"}},
                     {"user": {"login": "github-actions[bot]"}}], None)
        raise AssertionError(path)

    monkeypatch.setattr(km, "_gh_api", fake_gh_api)

    data = km._collect_actors()
    actors = {a["login"]: a for a in data["actors"]}

    assert data["ok"] is True
    assert data["n_repos"] == 1
    assert data["n_actors"] == 4
    assert actors["alice"]["commits"] == 3
    assert actors["alice"]["prs"] == 1
    assert actors["bob"]["prs"] == 1
    assert actors["bob"]["merged_prs"] == 1
    assert actors["carol"]["comments"] == 1
    assert actors["github-actions[bot]"]["kind"] == "bot"


def test_read_actors_disabled_when_org_is_blank():
    km.CFG.github_org = ""

    data = km.read_actors()

    assert data["ok"] is False
    assert data["disabled"] is True
    assert data["actors"] == []


def test_refresh_keeps_last_good_roster_when_next_fetch_fails(monkeypatch):
    results = iter([
        {"ok": True, "org": "knitweb", "actors": [{"login": "alice", "kind": "human"}],
         "repos": ["repo1"], "n_actors": 1, "n_repos": 1, "humans": 1, "bots": 0, "rate_note": None},
        {"ok": False, "org": "knitweb", "error": "TimeoutError",
         "actors": [], "repos": [], "n_actors": 0, "n_repos": 0, "rate_note": None},
    ])
    monkeypatch.setattr(km, "_collect_actors", lambda: next(results))
    monkeypatch.setattr(km.time, "monotonic", lambda: 1000.0)

    km._actors_refresh_once()
    km._actors_refresh_once()

    data = km.read_actors()
    assert data["ok"] is True
    assert data["actors"] == [{"login": "alice", "kind": "human"}]
    assert data["stale_error"] == "TimeoutError"


def test_refresh_records_unexpected_collector_exception(monkeypatch):
    monkeypatch.setattr(km, "_collect_actors", lambda: (_ for _ in ()).throw(RuntimeError("boom")))

    km._actors_refresh_once()
    data = km.read_actors()

    assert data["ok"] is False
    assert data["error"] == "RuntimeError"
