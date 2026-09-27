"""Reddit OAuth account connection (Stage 4 — authorization-code flow).

Implements the user-authorized OAuth layer against a *web* Reddit app,
following Reddit's official OAuth2 wiki (reddit-archive/reddit):

- Authorize: ``GET https://www.reddit.com/api/v1/authorize`` with
  ``client_id``, ``response_type=code``, ``state``, ``redirect_uri``,
  ``duration=permanent`` and a space-separated ``scope`` list.
- Token: ``POST https://www.reddit.com/api/v1/access_token`` with HTTP
  Basic auth (client_id:client_secret) and form fields
  ``grant_type=authorization_code&code=...&redirect_uri=...``
  (the redirect URI must match the registered one exactly, again).
- Refresh: ``POST`` the same endpoint with
  ``grant_type=refresh_token&refresh_token=...`` (refresh tokens are only
  issued when ``duration=permanent`` was requested).
- Identity: ``GET https://oauth.reddit.com/api/v1/me`` with the Bearer
  token; the username comes from this response, never from callback
  query parameters.

This is deliberately separate from the Stage 2 application-level
credentials (client_credentials grant against a *script* app), which
DiscoveryService keeps using for read-only discovery. The two
mechanisms are not conflated: different env vars, different app type,
different token lifecycle.

Security notes:
- ``state`` is cryptographically random, single-use, and expires after
  10 minutes. No token exchange happens unless state validates.
- Tokens live server-side in SQLite via RedditConnection. Plaintext
  SQLite storage is NOT encrypted secret storage (documented in README).
- Tokens never appear in logs, exception messages, templates, or URLs.
"""

from __future__ import annotations

import logging
import os
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
from dotenv import load_dotenv

from app.models import RedditConnection
from app.platforms.base import (
    RedditAPIError,
    RedditAuthError,
    RedditError,
    RedditNetworkError,
    RedditRateLimitError,
    RedditResponseError,
)
from app.repositories.reddit_connection_repository import (
    RedditConnectionRepository,
)

log = logging.getLogger(__name__)

AUTHORIZE_URL = "https://www.reddit.com/api/v1/authorize"
TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
IDENTITY_URL = "https://oauth.reddit.com/api/v1/me"

# Minimal scopes for current functionality: the connected account is used
# only for authenticated identity (`/api/v1/me`). Nothing reads Reddit
# content with the user token, so `identity` alone is the correct minimum.
OAUTH_SCOPES = ("identity",)

DEFAULT_REDIRECT_URI = "http://127.0.0.1:8000/auth/reddit/callback"
STATE_TTL_SECONDS = 10 * 60
# Refresh the access token when it expires within this margin.
REFRESH_MARGIN_SECONDS = 5 * 60


class RedditOAuthError(RedditError):
    """Base class for user-OAuth flow errors."""


class RedditOAuthConfigError(RedditOAuthError):
    """OAuth is not configured (missing env vars)."""


class RedditOAuthStateError(RedditOAuthError):
    """State missing, mismatched, expired, or reused."""


# ---------------------------------------------------------------------------
# Configuration — dedicated web-app credentials, never the Stage 2
# application-level (script app) credentials.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RedditOAuthConfig:
    client_id: str
    client_secret: str
    redirect_uri: str
    user_agent: str
    scopes: tuple[str, ...] = OAUTH_SCOPES

    @classmethod
    def from_env(cls, load_dotenv_file: bool = True) -> "RedditOAuthConfig":
        """Build config from environment.

        Requires a *web* Reddit app (authorization-code flow with a
        redirect URI). Raises RedditOAuthConfigError naming the missing
        variables — never including their values.
        """
        if load_dotenv_file:
            load_dotenv()
        missing = [
            name
            for name in (
                "SCOUTNEXUS_REDDIT_CLIENT_ID",
                "SCOUTNEXUS_REDDIT_CLIENT_SECRET",
            )
            if not os.environ.get(name)
        ]
        if missing:
            raise RedditOAuthConfigError(
                "Reddit OAuth is not configured. Missing: "
                + ", ".join(missing)
                + ". Register a *web* app at "
                "https://www.reddit.com/prefs/apps with redirect URI "
                f"{os.environ.get('SCOUTNEXUS_REDDIT_REDIRECT_URI', DEFAULT_REDIRECT_URI)!r}, "
                "then copy .env.example to .env and fill in the values."
            )
        user_agent = os.environ.get("SCOUTNEXUS_REDDIT_USER_AGENT") or os.environ.get(
            "REDDIT_USER_AGENT", ""
        )
        if not user_agent:
            raise RedditOAuthConfigError(
                "Reddit OAuth is not configured. Missing: "
                "SCOUTNEXUS_REDDIT_USER_AGENT (or REDDIT_USER_AGENT). "
                "Reddit requires a descriptive User-Agent."
            )
        return cls(
            client_id=os.environ["SCOUTNEXUS_REDDIT_CLIENT_ID"],
            client_secret=os.environ["SCOUTNEXUS_REDDIT_CLIENT_SECRET"],
            redirect_uri=os.environ.get(
                "SCOUTNEXUS_REDDIT_REDIRECT_URI", DEFAULT_REDIRECT_URI
            ),
            user_agent=user_agent,
        )

    def __repr__(self) -> str:  # never leak the secret in logs/tracebacks
        return (
            f"RedditOAuthConfig(client_id={self.client_id!r}, "
            f"client_secret='<redacted>', "
            f"redirect_uri={self.redirect_uri!r}, "
            f"user_agent={self.user_agent!r}, scopes={self.scopes!r})"
        )


# ---------------------------------------------------------------------------
# State store — cryptographically random, single-use, expiring.
# ---------------------------------------------------------------------------


class OAuthStateStore:
    """Minimal server-side store for OAuth ``state`` values.

    In-memory with a TTL: sufficient for the local single-process dev
    server. States are consumed exactly once — reuse is rejected.
    """

    def __init__(self, ttl_seconds: int = STATE_TTL_SECONDS):
        self._ttl = ttl_seconds
        self._states: dict[str, float] = {}
        self._lock = threading.Lock()

    def issue(self) -> str:
        state = secrets.token_urlsafe(32)
        with self._lock:
            self._purge_expired()
            self._states[state] = time.time() + self._ttl
        return state

    def consume(self, state: str | None) -> bool:
        """Validate and single-use-consume a state value.

        Returns False for missing, unknown, expired, or already-used
        states — all treated identically (no oracle for attackers).
        """
        if not state:
            return False
        with self._lock:
            self._purge_expired()
            # pop() makes consumption single-use: a replayed state is gone.
            return self._states.pop(state, None) is not None

    def _purge_expired(self) -> None:
        now = time.time()
        expired = [s for s, until in self._states.items() if until < now]
        for s in expired:
            del self._states[s]


# ---------------------------------------------------------------------------
# OAuth HTTP client
# ---------------------------------------------------------------------------


@dataclass
class TokenResult:
    access_token: str
    refresh_token: str | None
    expires_at: datetime | None
    scopes: str


class RedditOAuthClient:
    """Speaks Reddit's OAuth endpoints for the authorization-code flow."""

    def __init__(
        self,
        config: RedditOAuthConfig,
        client: httpx.Client | None = None,
        timeout: float = 15.0,
    ):
        self.config = config
        self._client = client or httpx.Client(timeout=timeout)

    # -- authorization -----------------------------------------------------

    def authorization_url(self, state: str) -> str:
        """Build the Reddit authorization URL for a fresh state value."""
        params = {
            "client_id": self.config.client_id,
            "response_type": "code",
            "state": state,
            "redirect_uri": self.config.redirect_uri,
            "duration": "permanent",  # request a refresh token
            "scope": " ".join(self.config.scopes),
        }
        return f"{AUTHORIZE_URL}?{urlencode(params)}"

    # -- token exchange / refresh ------------------------------------------

    def exchange_code(self, code: str) -> TokenResult:
        """Exchange an authorization code for tokens. One-time use."""
        payload = self._post_token(
            {
                "grant_type": "authorization_code",
                "code": code,
                # Must match the registered redirect URI exactly (again).
                "redirect_uri": self.config.redirect_uri,
            }
        )
        return self._to_token_result(payload, context="code exchange")

    def refresh(self, refresh_token: str) -> TokenResult:
        """Use a refresh token to obtain a new access token."""
        payload = self._post_token(
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            }
        )
        # Reddit may omit refresh_token here; keep the existing one then.
        result = self._to_token_result(payload, context="token refresh")
        if result.refresh_token is None:
            result = TokenResult(
                access_token=result.access_token,
                refresh_token=refresh_token,
                expires_at=result.expires_at,
                scopes=result.scopes,
            )
        return result

    def _post_token(self, data: dict[str, str]) -> Any:
        try:
            response = self._client.post(
                TOKEN_URL,
                data=data,
                auth=(self.config.client_id, self.config.client_secret),
                headers={"User-Agent": self.config.user_agent},
            )
        except httpx.TimeoutException as exc:
            raise RedditNetworkError(
                "Timed out while talking to Reddit's token endpoint"
            ) from exc
        except httpx.RequestError as exc:
            raise RedditNetworkError(
                f"Could not reach Reddit's token endpoint: {exc}"
            ) from exc
        if response.status_code == 401:
            raise RedditAuthError(
                "Reddit rejected the OAuth client credentials (HTTP 401). "
                "Check SCOUTNEXUS_REDDIT_CLIENT_ID and "
                "SCOUTNEXUS_REDDIT_CLIENT_SECRET."
            )
        if response.status_code == 429:
            raise RedditRateLimitError(
                "Reddit rate limit exceeded (HTTP 429) during token request."
            )
        if response.status_code >= 400:
            raise RedditAPIError(
                "Reddit token endpoint returned HTTP "
                f"{response.status_code}",
                status_code=response.status_code,
            )
        payload = self._parse_json(response, "token response")
        # Reddit signals grant problems inside the JSON body too.
        error = payload.get("error") if isinstance(payload, dict) else None
        if error:
            raise RedditAuthError(
                f"Reddit refused the token request ({error}). "
                "The code may have expired or already been used."
            )
        return payload

    # -- identity ----------------------------------------------------------

    def fetch_identity(self, access_token: str) -> dict[str, Any]:
        """Return the authenticated user's /api/v1/me payload.

        The username comes from this response — never from callback
        query parameters.
        """
        try:
            response = self._client.get(
                IDENTITY_URL,
                headers={
                    "User-Agent": self.config.user_agent,
                    # Token value stays in the header, never in a URL or log.
                    "Authorization": f"Bearer {access_token}",
                },
            )
        except httpx.TimeoutException as exc:
            raise RedditNetworkError(
                "Timed out while fetching the Reddit identity"
            ) from exc
        except httpx.RequestError as exc:
            raise RedditNetworkError(
                f"Could not reach Reddit's API: {exc}"
            ) from exc
        if response.status_code == 401:
            raise RedditAuthError(
                "Reddit rejected the access token (HTTP 401). "
                "It may have expired; reconnect the account."
            )
        if response.status_code >= 400:
            raise RedditAPIError(
                f"Reddit identity endpoint returned HTTP {response.status_code}",
                status_code=response.status_code,
            )
        payload = self._parse_json(response, "identity response")
        if not isinstance(payload, dict) or not payload.get("name"):
            raise RedditResponseError(
                "Reddit identity response did not contain a username"
            )
        return payload

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _parse_json(response: httpx.Response, context: str) -> Any:
        try:
            return response.json()
        except ValueError as exc:
            raise RedditResponseError(
                f"Could not parse Reddit {context} as JSON"
            ) from exc

    @staticmethod
    def _to_token_result(payload: Any, context: str) -> TokenResult:
        if not isinstance(payload, dict) or not payload.get("access_token"):
            raise RedditResponseError(
                f"Reddit {context} did not return an access token"
            )
        expires_in = payload.get("expires_in")
        try:
            expires_at = datetime.now() + timedelta(seconds=int(expires_in))
        except (TypeError, ValueError):
            expires_at = None
        return TokenResult(
            access_token=payload["access_token"],
            refresh_token=payload.get("refresh_token"),
            expires_at=expires_at,
            scopes=str(payload.get("scope", "")),
        )


# ---------------------------------------------------------------------------
# Refresh behavior — minimal: refresh only at/near expiry, then persist.
# ---------------------------------------------------------------------------


def token_needs_refresh(
    connection: RedditConnection,
    margin_seconds: int = REFRESH_MARGIN_SECONDS,
    at: datetime | None = None,
) -> bool:
    """True when the token is expired or expires within the margin.

    Unknown expiry (None) is treated as not needing refresh — we only
    refresh on evidence, never speculatively on every request.
    """
    if connection.token_expires_at is None or not connection.refresh_token:
        return False
    return (at or datetime.now()) >= connection.token_expires_at - timedelta(
        seconds=margin_seconds
    )


def refresh_connection(
    connection: RedditConnection,
    repo: RedditConnectionRepository,
    client: "RedditOAuthClient",
) -> RedditConnection:
    """Refresh an expiring access token and persist the new one."""
    if not connection.refresh_token:
        raise RedditOAuthError("No refresh token stored for this connection")
    result = client.refresh(connection.refresh_token)
    return repo.upsert(
        reddit_username=connection.reddit_username,
        access_token=result.access_token,
        refresh_token=result.refresh_token,
        token_expires_at=result.expires_at,
        scopes=result.scopes or connection.scopes,
    )


# Re-exported for the web layer's dependency wiring.
__all__ = [
    "AUTHORIZE_URL",
    "DEFAULT_REDIRECT_URI",
    "OAUTH_SCOPES",
    "OAuthStateStore",
    "RedditOAuthClient",
    "RedditOAuthConfig",
    "RedditOAuthConfigError",
    "RedditOAuthError",
    "RedditOAuthStateError",
    "TokenResult",
    "refresh_connection",
    "token_needs_refresh",
]
