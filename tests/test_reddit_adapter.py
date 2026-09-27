"""Reddit adapter tests — fully mocked, no live credentials required.

Uses httpx.MockTransport to simulate Reddit's OAuth2 token endpoint and
API responses. Covers configuration, normalization, search, errors,
rate limiting, URL generation, and the architecture boundary.
"""

from datetime import datetime, timezone

import httpx
import pytest

from app.platforms.base import PlatformAdapter
from app.platforms.reddit import (
    RedditAdapter,
    RedditAPIError,
    RedditAuthError,
    RedditConfig,
    RedditCredentialsError,
    RedditError,
    RedditNetworkError,
    RedditRateLimitError,
    RedditResponseError,
    reddit_status,
)

FAKE_CONFIG = RedditConfig(
    client_id="fake_id",
    client_secret="fake_secret",
    user_agent="test-agent/0.1",
)

TOKEN_BODY = {
    "access_token": "fake_token",
    "token_type": "bearer",
    "expires_in": 3600,
    "scope": "*",
}

POST_A = {
    "id": "abc123",
    "subreddit": "python",
    "author": "some_user",
    "title": "What AI coding tools are people using?",
    "selftext": "Looking for recommendations.",
    "permalink": "/r/python/comments/abc123/what_ai_coding_tools/",
    "created_utc": 1727222400.0,
    "num_comments": 34,
    "score": 128,
    "upvote_ratio": 0.94,
    "over_18": False,
    "is_self": True,
}


def listing_json(*posts):
    return {
        "kind": "Listing",
        "data": {
            "children": [{"kind": "t3", "data": p} for p in posts],
            "after": None,
            "before": None,
        },
    }


def build_adapter(api_responder, token_status=200, token_body=None):
    """Build an adapter wired to a mock transport.

    api_responder(request, calls) -> httpx.Response. `calls` records
    {"token": n, "api": [requests]}.
    """
    calls = {"token": 0, "api": []}

    def handler(request: httpx.Request) -> httpx.Response:
        if (
            request.url.host == "www.reddit.com"
            and request.url.path == "/api/v1/access_token"
        ):
            calls["token"] += 1
            return httpx.Response(token_status, json=token_body or TOKEN_BODY)
        calls["api"].append(request)
        return api_responder(request, calls)

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport, base_url="https://oauth.reddit.com")
    adapter = RedditAdapter(config=FAKE_CONFIG, client=client, retry_backoff=0)
    return adapter, calls


def ok_listing(*posts, headers=None):
    def responder(request, calls):
        return httpx.Response(200, json=listing_json(*posts), headers=headers or {})

    return responder


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def test_config_missing_credentials(monkeypatch):
    for var in ("REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_USER_AGENT"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(RedditCredentialsError) as exc_info:
        RedditConfig.from_env(load_dotenv_file=False)
    message = str(exc_info.value)
    assert "REDDIT_CLIENT_ID" in message
    assert "REDDIT_CLIENT_SECRET" in message
    assert "REDDIT_USER_AGENT" in message


def test_config_from_env(monkeypatch):
    monkeypatch.setenv("REDDIT_CLIENT_ID", "id123")
    monkeypatch.setenv("REDDIT_CLIENT_SECRET", "shh")
    monkeypatch.setenv("REDDIT_USER_AGENT", "ua/1.0")
    config = RedditConfig.from_env(load_dotenv_file=False)
    assert config.client_id == "id123"
    assert config.client_secret == "shh"
    assert config.user_agent == "ua/1.0"


def test_config_repr_redacts_secret():
    assert "fake_secret" not in repr(FAKE_CONFIG)
    assert "<redacted>" in repr(FAKE_CONFIG)


def test_reddit_status(monkeypatch):
    monkeypatch.setenv("REDDIT_CLIENT_ID", "id123")
    monkeypatch.setenv("REDDIT_CLIENT_SECRET", "shh")
    monkeypatch.setenv("REDDIT_USER_AGENT", "ua/1.0")
    assert reddit_status() == "Configured"
    for var in ("REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_USER_AGENT"):
        monkeypatch.delenv(var, raising=False)
    # load_dotenv_file=False equivalent: ensure no .env interferes by
    # checking the from_env path directly.
    with pytest.raises(RedditCredentialsError):
        RedditConfig.from_env(load_dotenv_file=False)


def test_adapter_implements_platform_interface():
    adapter, _ = build_adapter(ok_listing())
    assert isinstance(adapter, PlatformAdapter)
    assert RedditAdapter.__abstractmethods__ == set()
    assert RedditAdapter.platform == "reddit"


def test_adapter_does_not_self_register():
    # The demo MockRedditAdapter owns the "reddit" registry slot (seed script).
    # The real adapter needs credentials, so it is constructed explicitly.
    from app.platforms import ADAPTER_REGISTRY

    assert ADAPTER_REGISTRY.get("reddit") is not RedditAdapter


# ---------------------------------------------------------------------------
# Search / discovery
# ---------------------------------------------------------------------------


def test_discover_builds_site_wide_search_request():
    seen = {}

    def responder(request, calls):
        seen["path"] = request.url.path
        seen["params"] = dict(request.url.params)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json=listing_json(POST_A))

    adapter, calls = build_adapter(responder)
    results = adapter.discover("AI coding tools", limit=10)

    assert seen["path"] == "/search"
    assert seen["params"]["q"] == "AI coding tools"
    assert seen["params"]["limit"] == "10"
    assert seen["params"]["sort"] == "relevance"
    assert seen["params"]["t"] == "all"
    assert seen["auth"] == "Bearer fake_token"
    assert "restrict_sr" not in seen["params"]
    assert len(results) == 1
    assert calls["token"] == 1  # token fetched once


def test_discover_subreddit_search():
    seen = {}

    def responder(request, calls):
        seen["path"] = request.url.path
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json=listing_json(POST_A))

    adapter, _ = build_adapter(responder)
    results = adapter.discover(
        "coding tools", subreddit="ClaudeCode", limit=25, sort="new", time_filter="week"
    )
    assert seen["path"] == "/r/ClaudeCode/search"
    assert seen["params"]["restrict_sr"] == "1"
    assert seen["params"]["sort"] == "new"
    assert seen["params"]["t"] == "week"
    assert len(results) == 1


def test_discover_uses_default_subreddit():
    seen = {}

    def responder(request, calls):
        seen["path"] = request.url.path
        return httpx.Response(200, json=listing_json())

    adapter, _ = build_adapter(responder)
    adapter.default_subreddit = "python"
    assert adapter.discover("x") == []
    assert seen["path"] == "/r/python/search"


def test_discover_empty_results():
    adapter, _ = build_adapter(ok_listing())
    assert adapter.discover("no such thing xyz") == []


def test_discover_rejects_bad_params():
    adapter, _ = build_adapter(ok_listing())
    with pytest.raises(ValueError):
        adapter.discover("x", sort="bogus")
    with pytest.raises(ValueError):
        adapter.discover("x", time_filter="bogus")


def test_discover_parses_multiple_posts():
    post_b = dict(POST_A, id="zzz999", title="Second post")
    adapter, _ = build_adapter(ok_listing(POST_A, post_b))
    results = adapter.discover("x", limit=5)
    assert [r.source_id for r in results] == ["abc123", "zzz999"]


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def test_normalization_maps_all_fields():
    adapter, _ = build_adapter(ok_listing(POST_A))
    (post,) = adapter.discover("x")

    assert post.platform == "reddit"
    assert post.source_id == "abc123"
    assert post.community == "python"
    assert post.author == "some_user"
    assert post.title == "What AI coding tools are people using?"
    assert post.content == "Looking for recommendations."
    assert (
        post.url
        == "https://www.reddit.com/r/python/comments/abc123/what_ai_coding_tools/"
    )
    assert post.posted_at == datetime.fromtimestamp(
        1727222400.0, tz=timezone.utc
    ).replace(tzinfo=None)
    assert post.engagement["comments"] == 34
    assert post.engagement["score"] == 128
    assert post.engagement["upvote_ratio"] == 0.94
    assert post.engagement["over_18"] is False


def test_normalization_handles_deleted_author_and_missing_fields():
    sparse = {"id": "def456", "author": "[deleted]", "selftext": ""}
    adapter, _ = build_adapter(ok_listing(sparse))
    (post,) = adapter.discover("x")

    assert post.author == "[deleted]"
    assert post.content is None
    assert post.community is None
    assert post.title is None
    assert post.posted_at is None
    # Falls back to the canonical short URL without a permalink.
    assert post.url == "https://www.reddit.com/comments/def456/"


def test_build_url():
    adapter, _ = build_adapter(ok_listing())
    assert adapter.build_url("abc123") == "https://www.reddit.com/comments/abc123/"
    assert adapter.build_url("t3_abc123") == "https://www.reddit.com/comments/abc123/"


# ---------------------------------------------------------------------------
# fetch_post / fetch_comments / get_rules
# ---------------------------------------------------------------------------


def test_fetch_post():
    seen = {}

    def responder(request, calls):
        seen["path"] = request.url.path
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json=listing_json(POST_A))

    adapter, _ = build_adapter(responder)
    post = adapter.fetch_post("abc123")
    assert seen["path"] == "/api/info"
    assert seen["params"]["id"] == "t3_abc123"
    assert post is not None
    assert post.source_id == "abc123"
    assert post.title == "What AI coding tools are people using?"


def test_fetch_post_missing_returns_none():
    adapter, _ = build_adapter(ok_listing())
    assert adapter.fetch_post("nope") is None


def test_fetch_comments_parses_plain_dicts():
    comment_listing = {
        "kind": "Listing",
        "data": {
            "children": [
                {
                    "kind": "t1",
                    "data": {
                        "id": "c1",
                        "author": "commenter",
                        "body": "Great post!",
                        "score": 12,
                        "created_utc": 1727222500.0,
                    },
                },
                {"kind": "more", "data": {"children": ["c2", "c3"]}},
            ]
        },
    }

    def responder(request, calls):
        assert request.url.path == "/comments/abc123"
        return httpx.Response(200, json=[listing_json(POST_A), comment_listing])

    adapter, _ = build_adapter(responder)
    comments = adapter.fetch_comments("abc123", limit=10)
    assert len(comments) == 1  # "more" placeholder skipped
    assert comments[0]["author"] == "commenter"
    assert comments[0]["body"] == "Great post!"
    assert comments[0]["score"] == 12
    assert isinstance(comments[0]["created_at"], datetime)


def test_get_rules():
    def responder(request, calls):
        assert request.url.path == "/r/python/about/rules"
        return httpx.Response(
            200,
            json={
                "rules": [
                    {"short_name": "Be kind", "description": "Be excellent."},
                ]
            },
        )

    adapter, _ = build_adapter(responder)
    rules = adapter.get_rules("python")
    assert rules["community"] == "python"
    assert rules["rules"] == [{"name": "Be kind", "description": "Be excellent."}]


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


def test_token_auth_failure():
    adapter, _ = build_adapter(ok_listing(), token_status=401)
    with pytest.raises(RedditAuthError) as exc_info:
        adapter.discover("x")
    assert "fake_secret" not in str(exc_info.value)


def test_api_401_refreshes_token_once_then_succeeds():
    states = {"api_calls": 0}

    def responder(request, calls):
        states["api_calls"] += 1
        if states["api_calls"] == 1:
            return httpx.Response(401, json={"message": "Unauthorized"})
        return httpx.Response(200, json=listing_json(POST_A))

    adapter, calls = build_adapter(responder)
    results = adapter.discover("x")
    assert len(results) == 1
    assert calls["token"] == 2  # initial + forced refresh
    assert states["api_calls"] == 2


def test_api_401_twice_raises_auth_error():
    def responder(request, calls):
        return httpx.Response(401, json={"message": "Unauthorized"})

    adapter, _ = build_adapter(responder)
    with pytest.raises(RedditAuthError):
        adapter.discover("x")


def test_rate_limit_429_raises_without_retry():
    def responder(request, calls):
        return httpx.Response(
            429, json={"message": "Too Many Requests"}, headers={"retry-after": "30"}
        )

    adapter, calls = build_adapter(responder)
    with pytest.raises(RedditRateLimitError) as exc_info:
        adapter.discover("x")
    assert exc_info.value.retry_after == 30.0
    assert len(calls["api"]) == 1  # never retried — 429 is respected


def test_server_error_retries_with_bound_then_succeeds():
    states = {"n": 0}

    def responder(request, calls):
        states["n"] += 1
        if states["n"] < 3:
            return httpx.Response(500, json={})
        return httpx.Response(200, json=listing_json(POST_A))

    adapter, calls = build_adapter(responder)
    results = adapter.discover("x")
    assert len(results) == 1
    assert states["n"] == 3


def test_persistent_server_error_raises():
    def responder(request, calls):
        return httpx.Response(500, json={})

    adapter, calls = build_adapter(responder)
    with pytest.raises(RedditAPIError) as exc_info:
        adapter.discover("x")
    assert exc_info.value.status_code == 500
    assert len(calls["api"]) == 3  # 1 initial + 2 bounded retries


def test_timeout_becomes_network_error():
    def responder(request, calls):
        raise httpx.TimeoutException("timed out")

    adapter, _ = build_adapter(responder)
    with pytest.raises(RedditNetworkError):
        adapter.discover("x")


def test_connection_failure_becomes_network_error():
    def responder(request, calls):
        raise httpx.ConnectError("connection refused")

    adapter, _ = build_adapter(responder)
    with pytest.raises(RedditNetworkError):
        adapter.discover("x")


def test_malformed_json_becomes_response_error():
    def responder(request, calls):
        return httpx.Response(200, content=b"this is not json")

    adapter, _ = build_adapter(responder)
    with pytest.raises(RedditResponseError):
        adapter.discover("x")


def test_unexpected_shape_becomes_response_error():
    def responder(request, calls):
        return httpx.Response(200, json={"unexpected": "shape"})

    adapter, _ = build_adapter(responder)
    with pytest.raises(RedditResponseError):
        adapter.discover("x")


def test_token_cached_across_requests():
    adapter, calls = build_adapter(ok_listing(POST_A))
    adapter.discover("one")
    adapter.discover("two")
    assert calls["token"] == 1
    assert len(calls["api"]) == 2


def test_rate_limit_headers_are_tracked():
    headers = {
        "x-ratelimit-used": "12",
        "x-ratelimit-remaining": "48",
        "x-ratelimit-reset": "37",
    }
    adapter, _ = build_adapter(ok_listing(POST_A, headers=headers))
    adapter.discover("x")
    assert adapter.rate_limit["x-ratelimit-remaining"] == "48"


def test_no_secret_leaks_in_any_error():
    # Auth failure path must not echo credentials.
    adapter, _ = build_adapter(ok_listing(), token_status=401)
    try:
        adapter.discover("x")
        pytest.fail("expected RedditAuthError")
    except RedditError as exc:
        assert "fake_secret" not in str(exc)
        assert "fake_secret" not in repr(exc)


# ---------------------------------------------------------------------------
# Architecture boundary
# ---------------------------------------------------------------------------


def test_core_layers_do_not_import_reddit_adapter():
    """services/models/repositories/core must not depend on the Reddit
    implementation — only app.platforms.reddit and the web layer may."""
    from pathlib import Path

    app_dir = Path(__file__).resolve().parents[1] / "app"
    offenders = []
    for sub in ("services", "models", "repositories", "core"):
        for path in (app_dir / sub).rglob("*.py"):
            text = path.read_text()
            if "platforms.reddit" in text or "platforms import" in text and "Reddit" in text:
                offenders.append(str(path))
    assert offenders == []
