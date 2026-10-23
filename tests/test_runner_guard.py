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
