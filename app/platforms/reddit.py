"""Reddit platform adapter (Stage 2 — read-only).

Implements the PlatformAdapter interface against Reddit's official OAuth2
API using the application-only ``client_credentials`` grant (no user
account needed for public read access).

Verified against Reddit's official OAuth2 wiki (reddit-archive/reddit):

- Register a **script** app at https://www.reddit.com/prefs/apps to get a
  client_id + client_secret (confidential client, runs on your hardware).
- Token: ``POST https://www.reddit.com/api/v1/access_token`` with HTTP
  Basic auth (client_id:client_secret) and ``grant_type=client_credentials``.
- API calls go to ``https://oauth.reddit.com`` (NOT www.reddit.com) with
  ``Authorization: Bearer <token>``.
- A descriptive ``User-Agent`` is mandatory:
  ``<platform>:<app-id>:<version> (by /u/<username>)``.
- App-only tokens expire after ~1 hour (``expires_in``); there is no
  refresh token — the adapter re-requests one when needed.
- Rate limit is ~60 requests/minute for OAuth; responses carry
  ``X-Ratelimit-Used/Remaining/Reset`` headers.

This module is the ONLY place Reddit-specific knowledge lives. Everything
above it consumes NormalizedSource / plain dicts.

Read-only by design: no posting, no commenting, no voting.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx
from dotenv import load_dotenv

from app.platforms.base import (
    NormalizedSource,
    PlatformAdapter,
    RedditAPIError,
    RedditAuthError,
    RedditCredentialsError,
    RedditError,
    RedditNetworkError,
    RedditRateLimitError,
    RedditResponseError,
)

# Re-exported so existing imports (app.platforms.reddit / app.platforms)
# keep working; the classes are defined in app.platforms.base.
__all__ = [
    "RedditAdapter",
    "RedditAPIError",
    "RedditAuthError",
    "RedditCredentialsError",
    "RedditError",
    "RedditNetworkError",
    "RedditRateLimitError",
    "RedditResponseError",
]

log = logging.getLogger(__name__)

TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
API_BASE_URL = "https://oauth.reddit.com"
PUBLIC_BASE_URL = "https://www.reddit.com"

# NOTE: RedditAdapter intentionally does NOT self-register in
# ADAPTER_REGISTRY. The registry maps platform -> default adapter and the
# demo-only MockRedditAdapter owns "reddit" there (used by the seed script).
# The real adapter requires credentials, so it is constructed explicitly.

_SEARCH_SORTS = frozenset({"relevance", "new", "hot", "top", "comments"})
_TIME_FILTERS = frozenset({"hour", "day", "week", "month", "year", "all"})


# ---------------------------------------------------------------------------
# Configuration — environment-based, never hardcoded.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RedditConfig:
    client_id: str
    client_secret: str
    user_agent: str

    @classmethod
    def from_env(cls, load_dotenv_file: bool = True) -> "RedditConfig":
        """Build config from environment (optionally loading a local .env).

        Raises RedditCredentialsError naming the missing variables — never
        including their values.
        """
        if load_dotenv_file:
            load_dotenv()  # no-op when python-dotenv finds no .env file
        missing = [
            name
            for name in ("REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_USER_AGENT")
            if not os.environ.get(name)
        ]
        if missing:
            raise RedditCredentialsError(
                "Reddit credentials not configured. Missing: "
                + ", ".join(missing)
                + ". Copy .env.example to .env and fill in the values."
            )
        return cls(
            client_id=os.environ["REDDIT_CLIENT_ID"],
            client_secret=os.environ["REDDIT_CLIENT_SECRET"],
            user_agent=os.environ["REDDIT_USER_AGENT"],
        )

    def __repr__(self) -> str:  # never leak the secret in logs/tracebacks
        return (
            f"RedditConfig(client_id={self.client_id!r}, "
            f"client_secret='<redacted>', user_agent={self.user_agent!r})"
        )


def reddit_status() -> str:
    """One-word UI status that never raises: 'Configured' / 'Not configured'."""
    try:
        RedditConfig.from_env()
        return "Configured"
    except RedditCredentialsError:
        return "Not configured"


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class RedditAdapter(PlatformAdapter):
    """Read-only Reddit integration behind the PlatformAdapter interface."""

    platform = "reddit"

    def __init__(
        self,
        config: RedditConfig | None = None,
        client: httpx.Client | None = None,
        default_subreddit: str | None = None,
        timeout: float = 15.0,
        max_retries: int = 2,
        retry_backoff: float = 1.0,
    ):
        self.config = config or RedditConfig.from_env()
        self.default_subreddit = default_subreddit
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff
        self._client = client or httpx.Client(
            base_url=API_BASE_URL, timeout=timeout
        )
        self._token: str | None = None
        self._token_expires_at: float = 0.0
        # Last seen rate-limit headers, e.g. {"used": "12", "remaining": "48"}.
        self.rate_limit: dict[str, str] = {}

    # -- authentication ----------------------------------------------------

    def _ensure_token(self, force_refresh: bool = False) -> str:
        if (
            not force_refresh
            and self._token
            and time.time() < self._token_expires_at
        ):
            return self._token
        log.debug("Requesting Reddit app-only access token")
        try:
            response = self._client.post(
                TOKEN_URL,  # absolute URL overrides the oauth base_url
                data={"grant_type": "client_credentials"},
                auth=(self.config.client_id, self.config.client_secret),
                headers={"User-Agent": self.config.user_agent},
            )
        except httpx.TimeoutException as exc:
            raise RedditNetworkError(
                "Timed out while requesting Reddit access token"
            ) from exc
        except httpx.RequestError as exc:
            raise RedditNetworkError(
                f"Could not reach Reddit token endpoint: {exc}"
            ) from exc

        if response.status_code == 401:
            raise RedditAuthError(
                "Reddit rejected the client credentials (HTTP 401). "
                "Check REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET."
            )
        if response.status_code != 200:
            raise RedditAPIError(
                f"Reddit token endpoint returned HTTP {response.status_code}",
                status_code=response.status_code,
            )
        payload = self._parse_json(response, "access token response")
        token = payload.get("access_token")
        if not token:
            raise RedditResponseError(
                "Reddit token response did not contain an access_token"
            )
        # expires_in is ~3600s; refresh a minute early.
        expires_in = payload.get("expires_in", 3600)
        try:
            margin = max(0, int(expires_in) - 60)
        except (TypeError, ValueError):
            margin = 3540
        self._token = token
        self._token_expires_at = time.time() + margin
        return token

    # -- low-level request handling -----------------------------------------

    def _api_request(
        self, method: str, path: str, params: dict[str, Any] | None = None
    ) -> Any:
        """Send one authenticated API request with error/rate-limit handling.

        - 401: refresh the token once, then retry once.
        - 429: raise RedditRateLimitError (never retried here).
        - 5xx: bounded retry with exponential backoff.
        - timeouts / connection errors: RedditNetworkError.
        """
        token = self._ensure_token()
        attempts = 0
        refreshed = False
        while True:
            try:
                response = self._client.request(
                    method,
                    path,
                    params=params,
                    headers={
                        "User-Agent": self.config.user_agent,
                        "Authorization": f"Bearer {token}",
                    },
                )
            except httpx.TimeoutException as exc:
                raise RedditNetworkError(
                    f"Reddit API request timed out ({method} {path})"
                ) from exc
            except httpx.RequestError as exc:
                raise RedditNetworkError(
                    f"Could not reach Reddit API ({method} {path}): {exc}"
                ) from exc

            self._track_rate_limit(response.headers)

            if response.status_code == 401 and not refreshed:
                # Token may have expired early; refresh once and retry.
                refreshed = True
                token = self._ensure_token(force_refresh=True)
                continue
            if response.status_code == 401:
                raise RedditAuthError(
                    "Reddit rejected the access token (HTTP 401). "
                    "Check REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET."
                )
            if response.status_code == 429:
                raise self._rate_limit_error(response)
            if 500 <= response.status_code < 600 and attempts < self.max_retries:
                attempts += 1
                delay = self.retry_backoff * (2 ** (attempts - 1))
                log.warning(
                    "Reddit returned HTTP %s; retry %d/%d after %.1fs",
                    response.status_code,
                    attempts,
                    self.max_retries,
                    delay,
                )
                time.sleep(delay)
                continue
            if response.status_code >= 400:
                raise RedditAPIError(
                    f"Reddit API returned HTTP {response.status_code} "
                    f"for {method} {path}",
                    status_code=response.status_code,
                )
            return self._parse_json(response, f"{method} {path}")

    @staticmethod
    def _parse_json(response: httpx.Response, context: str) -> Any:
        try:
            return response.json()
        except ValueError as exc:
            raise RedditResponseError(
                f"Could not parse Reddit {context} as JSON"
            ) from exc

    def _track_rate_limit(self, headers: httpx.Headers) -> None:
        seen = {
            key: headers[key]
            for key in ("x-ratelimit-used", "x-ratelimit-remaining", "x-ratelimit-reset")
            if key in headers
        }
        if seen:
            self.rate_limit = seen

    @staticmethod
    def _rate_limit_error(response: httpx.Response) -> RedditRateLimitError:
        retry_after: float | None = None
        raw = response.headers.get("retry-after")
        if raw is not None:
            try:
                retry_after = float(raw)
            except ValueError:
                retry_after = None
        hint = (
            f" Retry after {retry_after:.0f}s."
            if retry_after is not None
            else " Check X-Ratelimit-Reset."
        )
        return RedditRateLimitError(
            "Reddit rate limit exceeded (HTTP 429)." + hint,
            retry_after=retry_after,
        )

    # -- PlatformAdapter interface ------------------------------------------

    def discover(
        self,
        query: str,
        limit: int = 25,
        *,
        subreddit: str | None = None,
        sort: str = "relevance",
        time_filter: str = "all",
    ) -> list[NormalizedSource]:
        """Search Reddit posts. Extra kwargs keep Reddit-specific search
        options inside the adapter; the base (query, limit) signature still
        satisfies the PlatformAdapter contract."""
        if sort not in _SEARCH_SORTS:
            raise ValueError(
                f"Unknown sort {sort!r}. Choose from {sorted(_SEARCH_SORTS)}"
            )
        if time_filter not in _TIME_FILTERS:
            raise ValueError(
                f"Unknown time_filter {time_filter!r}. "
                f"Choose from {sorted(_TIME_FILTERS)}"
            )
        target = subreddit or self.default_subreddit
        params: dict[str, Any] = {
            "q": query,
            "sort": sort,
            "t": time_filter,
            "limit": max(1, min(limit, 100)),
            "type": "link",
        }
        if target:
            path = f"/r/{target}/search"
            params["restrict_sr"] = "1"
        else:
            path = "/search"
        payload = self._api_request("GET", path, params=params)
        return [
            self._normalize_post(child["data"])
            for child in self._listing_children(payload)
            if child.get("kind") == "t3" and isinstance(child.get("data"), dict)
        ]

    def fetch_post(self, source_id: str) -> NormalizedSource | None:
        """Fetch a single post by its Reddit ID (bare ID or t3_ fullname)."""
        post_id = source_id.removeprefix("t3_")
        payload = self._api_request("GET", "/api/info", params={"id": f"t3_{post_id}"})
        children = self._listing_children(payload)
        for child in children:
            data = child.get("data")
            if isinstance(data, dict) and data.get("id") == post_id:
                return self._normalize_post(data)
        return None

    def fetch_comments(
        self, source_id: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Fetch comments as plain dicts (never Reddit API objects)."""
        post_id = source_id.removeprefix("t3_")
        payload = self._api_request(
            "GET", f"/comments/{post_id}", params={"limit": max(1, min(limit, 100))}
        )
        if not isinstance(payload, list) or len(payload) < 2:
            raise RedditResponseError(
                "Unexpected comments response shape from Reddit"
            )
        comments: list[dict[str, Any]] = []
        for child in self._listing_children(payload[1]):
            if child.get("kind") != "t1":
                continue  # skip "more" placeholders etc.
            data = child.get("data")
            if not isinstance(data, dict):
                continue
            comments.append(
                {
                    "id": data.get("id"),
                    "author": data.get("author"),
                    "body": data.get("body"),
                    "score": data.get("score"),
                    "created_at": self._to_datetime(data.get("created_utc")),
                }
            )
        return comments

    def get_rules(self, community: str) -> dict[str, Any]:
        payload = self._api_request("GET", f"/r/{community}/about/rules")
        if not isinstance(payload, dict):
            raise RedditResponseError("Unexpected rules response shape from Reddit")
        rules = [
            {"name": r.get("short_name"), "description": r.get("description")}
            for r in payload.get("rules", [])
            if isinstance(r, dict)
        ]
        return {"community": community, "rules": rules}

    def build_url(self, source_id: str) -> str:
        """Canonical public post URL. Reddit redirects /comments/<id>/ to the
        full permalink, so this works without knowing the title slug."""
        post_id = source_id.removeprefix("t3_")
        return f"{PUBLIC_BASE_URL}/comments/{post_id}/"

    # -- normalization --------------------------------------------------------

    def _normalize_post(self, data: dict[str, Any]) -> NormalizedSource:
        permalink = data.get("permalink")
        url = f"{PUBLIC_BASE_URL}{permalink}" if permalink else self.build_url(
            data.get("id", "")
        )
        engagement = {
            "comments": data.get("num_comments"),
            "score": data.get("score"),
            "upvote_ratio": data.get("upvote_ratio"),
            "over_18": data.get("over_18"),
            "is_self": data.get("is_self"),
        }
        return NormalizedSource(
            platform=self.platform,
            source_id=str(data.get("id", "")),
            community=data.get("subreddit"),
            author=data.get("author"),  # "[deleted]" passes through as-is
            title=data.get("title"),
            content=data.get("selftext") or None,
            url=url,
            posted_at=self._to_datetime(data.get("created_utc")),
            engagement=engagement,
        )

    @staticmethod
    def _listing_children(payload: Any) -> list[dict[str, Any]]:
        if not isinstance(payload, dict):
            raise RedditResponseError("Unexpected listing response shape from Reddit")
        children = payload.get("data", {}).get("children")
        if not isinstance(children, list):
            raise RedditResponseError("Unexpected listing response shape from Reddit")
        return children

    @staticmethod
    def _to_datetime(value: Any) -> datetime | None:
        try:
            stamp = float(value)
        except (TypeError, ValueError):
            return None
        # Naive UTC for consistency with the rest of the codebase.
        return datetime.fromtimestamp(stamp, tz=timezone.utc).replace(tzinfo=None)
