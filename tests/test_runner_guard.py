"""The exploit runner must refuse any host that is not the configured staging
origin. This fail-closed guard is a safety rail, so it gets its own tests."""
import pytest

from redteam.runner import HostNotAllowed, assert_allowed


def test_allows_exact_match():
    assert_allowed("http://staging.local/search?q=x", "http://staging.local")


def test_allows_trailing_slash_difference():
    assert_allowed("http://staging.local/", "http://staging.local")


def test_refuses_different_host():
    with pytest.raises(HostNotAllowed):
        assert_allowed("http://example.com", "http://staging.local")


def test_refuses_when_no_allowed_configured():
    with pytest.raises(HostNotAllowed):
        assert_allowed("http://staging.local", "")


def test_refuses_scheme_mismatch():
    with pytest.raises(HostNotAllowed):
        assert_allowed("https://staging.local", "http://staging.local")


def test_check_targets_refuses_stale_or_wrong_builds(monkeypatch):
    import httpx

    from redteam import check_targets

    state = {"http://pre": {"version": "c1", "secure_mode": False},
             "http://post": {"version": "c0", "secure_mode": True}}

    def get(url, timeout):
        base = url.rsplit("/healthz", 1)[0]
        return httpx.Response(200, json=state[base])

    monkeypatch.setattr(httpx, "get", get)
    assert check_targets.main(["--pre", "http://pre", "--commit", "c1"]) == 0
    assert check_targets.main(["--post", "http://post", "--commit", "c1"]) == 1  # stale
    state["http://post"]["version"] = "c1"
    assert check_targets.main(["--pre", "http://pre", "--post", "http://post", "--commit", "c1"]) == 0
    state["http://pre"]["secure_mode"] = True  # twin swapped for the hardened build
    assert check_targets.main(["--pre", "http://pre", "--commit", "c1"]) == 1
