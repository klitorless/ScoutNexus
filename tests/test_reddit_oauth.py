"""Stage 4 tests: Reddit OAuth account connection.

Everything here is mocked — no Reddit account, credentials, or live
network access. HTTP is faked with httpx.MockTransport; OAuth config
comes from monkeypatched environment variables.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.main import app, get_oauth_client, get_oauth_state_store
from app.models import RedditConnection
from app.platforms.reddit import (
    RedditAPIError,
    RedditAuthError,
    RedditNetworkError,
    RedditResponseError,
)
from app.repositories.reddit_connection_repository import (
    RedditConnectionRepository,
)
from app.services.reddit_oauth import (
    AUTHORIZE_URL,
    DEFAULT_REDIRECT_URI,
    OAuthStateStore,
    RedditOAuthClient,
    RedditOAuthConfig,
    RedditOAuthConfigError,
    RedditOAuthError,
    refresh_connection,
    token_needs_refresh,
)

OAUTH_ENV = {
    "SCOUTNEXUS_REDDIT_CLIENT_ID": "test-client-id",
    "SCOUTNEXUS_REDDIT_CLIENT_SECRET": "test-client-secret",
    "SCOUTNEXUS_REDDIT_USER_AGENT": "ScoutNexusOAuthTest/0.1",
}
CLEARED_ENV = (
    "SCOUTNEXUS_REDDIT_CLIENT_ID",
    "SCOUTNEXUS_REDDIT_CLIENT_SECRET",
    "SCOUTNEXUS_REDDIT_REDIRECT_URI",
    "SCOUTNEXUS_REDDIT_USER_AGENT",
    "REDDIT_USER_AGENT",
)
# Proxy env vars are cleared so ambient sandbox/proxy settings cannot
# interfere with (mocked) HTTP client construction in tests.
CLEARED_PROXY_ENV = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
)


def _clear_env(monkeypatch):
    for var in CLEARED_ENV + CLEARED_PROXY_ENV:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def oauth_env(monkeypatch):
    _clear_env(monkeypatch)
    for var, value in OAUTH_ENV.items():
        monkeypatch.setenv(var, value)


@pytest.fixture
def no_oauth_env(monkeypatch):
    _clear_env(monkeypatch)


def make_oauth_client(handler) -> tuple[RedditOAuthClient, list]:
    """Build a client over a MockTransport; record every request."""
    calls: list[httpx.Request] = []

    def recording_handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(request)

    config = RedditOAuthConfig(
        client_id="test-client-id",
        client_secret="test-client-secret",
        redirect_uri=DEFAULT_REDIRECT_URI,
        user_agent="ScoutNexusOAuthTest/0.1",
    )
    transport = httpx.MockTransport(recording_handler)
    return RedditOAuthClient(config, client=httpx.Client(transport=transport)), calls


def success_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/api/v1/access_token":
        return httpx.Response(
            200,
            json={
                "access_token": "ACCESS_TOKEN_VALUE",
                "token_type": "bearer",
                "expires_in": 3600,
                "scope": "identity",
                "refresh_token": "REFRESH_TOKEN_VALUE",
            },
        )
    if request.url.path == "/api/v1/me":
        return httpx.Response(200, json={"id": "t2_abc", "name": "test_redditor"})
    return httpx.Response(404, json={"error": "not found"})


@pytest.fixture
def harness(client, oauth_env):
    """TestClient with a fresh OAuth state store and a swappable mock."""
    store = OAuthStateStore()
    holder: dict = {}

    def use_handler(handler):
        mock, calls = make_oauth_client(handler)
        holder["calls"] = calls
        app.dependency_overrides[get_oauth_client] = lambda: mock

    app.dependency_overrides[get_oauth_state_store] = lambda: store
    use_handler(success_handler)
    return SimpleNamespace(client=client, store=store, use_handler=use_handler,
                           calls=lambda: holder["calls"])


def make_connection(session, **overrides) -> RedditConnection:
    repo = RedditConnectionRepository(session)
    fields = dict(
        reddit_username="test_redditor",
        access_token="ACCESS_TOKEN_VALUE",
        refresh_token="REFRESH_TOKEN_VALUE",
        token_expires_at=datetime.now() + timedelta(hours=1),
        scopes="identity",
    )
    fields.update(overrides)
    connection = repo.upsert(**fields)
    session.commit()
    return connection


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def test_oauth_config_requires_client_id_and_secret(no_oauth_env):
    with pytest.raises(RedditOAuthConfigError) as exc_info:
        RedditOAuthConfig.from_env(load_dotenv_file=False)
    message = str(exc_info.value)
    assert "SCOUTNEXUS_REDDIT_CLIENT_ID" in message
    assert "SCOUTNEXUS_REDDIT_CLIENT_SECRET" in message


def test_oauth_config_requires_user_agent(no_oauth_env, monkeypatch):
    monkeypatch.setenv("SCOUTNEXUS_REDDIT_CLIENT_ID", "id")
    monkeypatch.setenv("SCOUTNEXUS_REDDIT_CLIENT_SECRET", "secret")
    with pytest.raises(RedditOAuthConfigError) as exc_info:
        RedditOAuthConfig.from_env(load_dotenv_file=False)
    assert "USER_AGENT" in str(exc_info.value)


def test_oauth_config_defaults_redirect_uri(oauth_env):
    config = RedditOAuthConfig.from_env(load_dotenv_file=False)
    assert config.redirect_uri == DEFAULT_REDIRECT_URI
    assert config.scopes == ("identity",)


def test_oauth_config_respects_custom_redirect_uri(oauth_env, monkeypatch):
    monkeypatch.setenv(
        "SCOUTNEXUS_REDDIT_REDIRECT_URI", "https://example.com/auth/reddit/callback"
    )
    config = RedditOAuthConfig.from_env(load_dotenv_file=False)
    assert config.redirect_uri == "https://example.com/auth/reddit/callback"


def test_oauth_config_repr_redacts_secret(oauth_env):
    config = RedditOAuthConfig.from_env(load_dotenv_file=False)
    blob = repr(config)
    assert "test-client-secret" not in blob
    assert "<redacted>" in blob


# ---------------------------------------------------------------------------
# State store
# ---------------------------------------------------------------------------


def test_state_issue_is_random_and_nonempty():
    store = OAuthStateStore()
    first, second = store.issue(), store.issue()
    assert first and second
    assert first != second


def test_state_consume_is_single_use():
    store = OAuthStateStore()
    state = store.issue()
    assert store.consume(state) is True
    # Replay of the same state is rejected.
    assert store.consume(state) is False


def test_state_consume_rejects_unknown_and_empty():
    store = OAuthStateStore()
    assert store.consume("never-issued") is False
    assert store.consume("") is False
    assert store.consume(None) is False


def test_state_expires():
    store = OAuthStateStore(ttl_seconds=-1)
    assert store.consume(store.issue()) is False


# ---------------------------------------------------------------------------
# OAuth client — authorization URL
# ---------------------------------------------------------------------------


def test_authorization_url_parameters(oauth_env):
    config = RedditOAuthConfig.from_env(load_dotenv_file=False)
    client = RedditOAuthClient(config, client=httpx.Client())
    url = client.authorization_url("STATE123")
    assert url.startswith(AUTHORIZE_URL)
    query = parse_qs(urlparse(url).query)
    assert query["client_id"] == ["test-client-id"]
    assert query["response_type"] == ["code"]
    assert query["state"] == ["STATE123"]
    assert query["redirect_uri"] == [DEFAULT_REDIRECT_URI]
    assert query["duration"] == ["permanent"]  # requests a refresh token
    assert query["scope"] == ["identity"]
    # The secret must never travel in the authorization URL.
    assert "test-client-secret" not in url


# ---------------------------------------------------------------------------
# OAuth client — token exchange / refresh / identity (all mocked)
# ---------------------------------------------------------------------------


def test_exchange_code_success():
    client, calls = make_oauth_client(success_handler)
    result = client.exchange_code("AUTH_CODE")
    assert result.access_token == "ACCESS_TOKEN_VALUE"
    assert result.refresh_token == "REFRESH_TOKEN_VALUE"
    assert result.scopes == "identity"
    assert result.expires_at is not None
    assert result.expires_at > datetime.now() + timedelta(minutes=50)

    request = calls[0]
    assert request.url.path == "/api/v1/access_token"
    # HTTP Basic auth (client_id:client_secret), never in the URL or body.
    assert request.headers["authorization"].startswith("Basic ")
    assert "test-client-secret" not in str(request.url)
    body = request.read().decode()
    assert "grant_type=authorization_code" in body
    assert "code=AUTH_CODE" in body


def test_exchange_code_401_maps_to_auth_error():
    def handler(request):
        return httpx.Response(401, json={"error": "invalid_client"})

    client, _ = make_oauth_client(handler)
    with pytest.raises(RedditAuthError):
        client.exchange_code("AUTH_CODE")


def test_exchange_code_json_error_maps_to_auth_error():
    def handler(request):
        return httpx.Response(200, json={"error": "invalid_grant"})

    client, _ = make_oauth_client(handler)
    with pytest.raises(RedditAuthError):
        client.exchange_code("AUTH_CODE")


def test_exchange_code_network_failure():
    def handler(request):
        raise httpx.ConnectError("boom")

    client, _ = make_oauth_client(handler)
    with pytest.raises(RedditNetworkError):
        client.exchange_code("AUTH_CODE")


def test_exchange_code_bad_json():
    def handler(request):
        return httpx.Response(200, content=b"not json")

    client, _ = make_oauth_client(handler)
    with pytest.raises(RedditResponseError):
        client.exchange_code("AUTH_CODE")


def test_exchange_code_missing_access_token():
    def handler(request):
        return httpx.Response(200, json={"token_type": "bearer"})

    client, _ = make_oauth_client(handler)
    with pytest.raises(RedditResponseError):
        client.exchange_code("AUTH_CODE")


def test_refresh_keeps_existing_refresh_token_when_omitted():
    def handler(request):
        return httpx.Response(
            200,
            json={"access_token": "NEW_ACCESS", "expires_in": 3600, "scope": "identity"},
        )

    client, _ = make_oauth_client(handler)
    result = client.refresh("OLD_REFRESH")
    assert result.access_token == "NEW_ACCESS"
    assert result.refresh_token == "OLD_REFRESH"


def test_fetch_identity_returns_username():
    client, calls = make_oauth_client(success_handler)
    identity = client.fetch_identity("ACCESS_TOKEN_VALUE")
    assert identity["name"] == "test_redditor"
    request = calls[0]
    assert str(request.url).startswith("https://oauth.reddit.com/api/v1/me")
    assert request.headers["authorization"] == "Bearer ACCESS_TOKEN_VALUE"


def test_fetch_identity_401_maps_to_auth_error():
    def handler(request):
        return httpx.Response(401, json={"error": "unauthorized"})

    client, _ = make_oauth_client(handler)
    with pytest.raises(RedditAuthError):
        client.fetch_identity("STALE_TOKEN")


def test_fetch_identity_missing_name():
    def handler(request):
        return httpx.Response(200, json={"id": "t2_abc"})

    client, _ = make_oauth_client(handler)
    with pytest.raises(RedditResponseError):
        client.fetch_identity("ACCESS_TOKEN_VALUE")


# ---------------------------------------------------------------------------
# Refresh behavior helpers
# ---------------------------------------------------------------------------


def test_token_needs_refresh(session):
    fresh = make_connection(session)
    assert token_needs_refresh(fresh) is False

    expiring_soon = make_connection(
        session,
        reddit_username="u2",
        token_expires_at=datetime.now() + timedelta(minutes=2),
    )
    assert token_needs_refresh(expiring_soon) is True

    expired = make_connection(
        session,
        reddit_username="u3",
        token_expires_at=datetime.now() - timedelta(minutes=1),
    )
    assert token_needs_refresh(expired) is True

    unknown_expiry = make_connection(
        session, reddit_username="u4", token_expires_at=None
    )
    assert token_needs_refresh(unknown_expiry) is False

    no_refresh_token = make_connection(
        session,
        reddit_username="u5",
        refresh_token=None,
        token_expires_at=datetime.now() - timedelta(minutes=1),
    )
    assert token_needs_refresh(no_refresh_token) is False


def test_refresh_connection_persists_new_token(session):
    def handler(request):
        return httpx.Response(
            200,
            json={
                "access_token": "ROTATED_ACCESS",
                "expires_in": 3600,
                "scope": "identity",
            },
        )

    client, _ = make_oauth_client(handler)
    connection = make_connection(
        session, token_expires_at=datetime.now() - timedelta(minutes=1)
    )
    refreshed = refresh_connection(
        connection, RedditConnectionRepository(session), client
    )
    session.commit()
    assert refreshed.access_token == "ROTATED_ACCESS"
    assert refreshed.refresh_token == "REFRESH_TOKEN_VALUE"  # kept
    assert token_needs_refresh(refreshed) is False


def test_refresh_connection_without_refresh_token_raises(session):
    client, _ = make_oauth_client(success_handler)
    connection = make_connection(session, refresh_token=None)
    with pytest.raises(RedditOAuthError):
        refresh_connection(connection, RedditConnectionRepository(session), client)


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------


def test_connection_upsert_and_get_active(session):
    repo = RedditConnectionRepository(session)
    assert repo.get_active() is None
    first = make_connection(session)
    assert repo.get_active().id == first.id
    # Reconnecting the same user updates tokens instead of duplicating.
    second = repo.upsert(
        reddit_username="test_redditor",
        access_token="SECOND_ACCESS",
        refresh_token=None,
        token_expires_at=None,
        scopes="identity",
    )
    session.commit()
    assert second.id == first.id
    assert repo.get_active().access_token == "SECOND_ACCESS"


def test_connection_repr_redacts_tokens(session):
    connection = make_connection(session)
    blob = repr(connection)
    assert "ACCESS_TOKEN_VALUE" not in blob
    assert "REFRESH_TOKEN_VALUE" not in blob
    assert "test_redditor" in blob


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def _location_query(response):
    return parse_qs(urlparse(response.headers["location"]).query)


def test_auth_start_redirects_to_reddit_with_state(harness):
    response = harness.client.get("/auth/reddit", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"].startswith(AUTHORIZE_URL)
    query = _location_query(response)
    assert query["response_type"] == ["code"]
    assert query["duration"] == ["permanent"]
    assert query["scope"] == ["identity"]
    assert query["redirect_uri"] == [DEFAULT_REDIRECT_URI]
    state = query["state"][0]
    assert state  # non-empty, non-static
    # The state was tracked server-side: consuming it works exactly once.
    assert harness.store.consume(state) is True


def test_auth_start_state_is_random(harness):
    first = _location_query(harness.client.get("/auth/reddit", follow_redirects=False))["state"][0]
    second = _location_query(harness.client.get("/auth/reddit", follow_redirects=False))["state"][0]
    assert first != second


def test_auth_start_not_configured(client, no_oauth_env):
    response = client.get("/auth/reddit", follow_redirects=False)
    assert response.status_code == 400
    assert "not configured" in response.text.lower()
    assert "location" not in response.headers  # no redirect happened


def test_auth_start_already_connected_redirects(harness, session):
    make_connection(session)
    response = harness.client.get("/auth/reddit", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/discovery"


def test_callback_success_persists_connection(harness, session):
    state = harness.store.issue()
    response = harness.client.get(
        f"/auth/reddit/callback?code=AUTH_CODE&state={state}", follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/discovery"
    repo = RedditConnectionRepository(session)
    connection = repo.get_active()
    assert connection is not None
    # Identity came from Reddit's API response, not from query params.
    assert connection.reddit_username == "test_redditor"
    assert connection.access_token == "ACCESS_TOKEN_VALUE"
    assert connection.refresh_token == "REFRESH_TOKEN_VALUE"
    assert connection.scopes == "identity"


def test_callback_ignores_username_query_param(harness, session):
    state = harness.store.issue()
    response = harness.client.get(
        f"/auth/reddit/callback?code=AUTH_CODE&state={state}&username=attacker",
        follow_redirects=False,
    )
    assert response.status_code == 303
    connection = RedditConnectionRepository(session).get_active()
    assert connection.reddit_username == "test_redditor"


def test_callback_invalid_state_attempts_no_exchange(harness, session):
    response = harness.client.get(
        "/auth/reddit/callback?code=AUTH_CODE&state=bogus-state"
    )
    assert response.status_code == 400
    assert "not completed" in response.text.lower()
    assert harness.calls() == []  # token endpoint never touched
    assert RedditConnectionRepository(session).get_active() is None


def test_callback_missing_state_attempts_no_exchange(harness, session):
    response = harness.client.get("/auth/reddit/callback?code=AUTH_CODE")
    assert response.status_code == 400
    assert harness.calls() == []


def test_callback_missing_code_attempts_no_exchange(harness):
    state = harness.store.issue()
    response = harness.client.get(f"/auth/reddit/callback?state={state}")
    assert response.status_code == 400
    assert harness.calls() == []


def test_callback_reused_state_rejected(harness, session):
    state = harness.store.issue()
    assert harness.store.consume(state) is True  # consumed elsewhere first
    response = harness.client.get(
        f"/auth/reddit/callback?code=AUTH_CODE&state={state}"
    )
    assert response.status_code == 400
    assert harness.calls() == []
    assert RedditConnectionRepository(session).get_active() is None


def test_callback_denied_authorization(harness, session):
    response = harness.client.get("/auth/reddit/callback?error=access_denied")
    assert response.status_code == 400
    assert "not completed" in response.text.lower()
    assert harness.calls() == []
    assert RedditConnectionRepository(session).get_active() is None


def test_callback_exchange_failure_shows_safe_error(harness, session):
    def handler(request):
        return httpx.Response(401, json={"error": "invalid_client"})

    harness.use_handler(handler)
    state = harness.store.issue()
    response = harness.client.get(
        f"/auth/reddit/callback?code=AUTH_CODE&state={state}", follow_redirects=False
    )
    assert response.status_code == 502
    assert "not completed" in response.text.lower()
    # No secrets leak into the error page.
    for secret in ("test-client-secret", "ACCESS_TOKEN_VALUE", "REFRESH_TOKEN_VALUE", "AUTH_CODE"):
        assert secret not in response.text
    assert RedditConnectionRepository(session).get_active() is None


def test_disconnect_removes_connection(harness, session):
    make_connection(session)
    response = harness.client.post("/auth/reddit/disconnect", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/discovery"
    assert RedditConnectionRepository(session).get_active() is None


def test_discovery_console_shows_connect_when_unconnected(harness):
    response = harness.client.get("/discovery")
    assert response.status_code == 200
    assert 'href="/auth/reddit"' in response.text
    assert "Connect Reddit" in response.text


def test_discovery_console_shows_connected_state(harness, session):
    make_connection(session)
    response = harness.client.get("/discovery")
    assert response.status_code == 200
    assert "test_redditor" in response.text
    assert "Disconnect Reddit" in response.text
    for secret in ("test-client-secret", "ACCESS_TOKEN_VALUE", "REFRESH_TOKEN_VALUE"):
        assert secret not in response.text


def test_discovery_console_oauth_not_configured(client, no_oauth_env):
    response = client.get("/discovery")
    assert response.status_code == 200
    assert "not configured" in response.text.lower()
    assert 'href="/auth/reddit"' not in response.text


def test_oauth_connection_does_not_create_candidates(harness, session):
    """The OAuth flow touches connections only — no candidates, no sources."""
    from app.repositories.candidate_repository import CandidateRepository
    from app.repositories.source_repository import SourceRepository

    state = harness.store.issue()
    harness.client.get(
        f"/auth/reddit/callback?code=AUTH_CODE&state={state}", follow_redirects=False
    )
    assert CandidateRepository(session).list() == []
    assert SourceRepository(session).list() == []
