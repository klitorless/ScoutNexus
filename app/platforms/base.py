"""Platform adapter interface.

Core application logic must NEVER depend directly on a platform's API
objects (e.g. PRAW models). Every platform integration implements
PlatformAdapter and speaks only in NormalizedSource / plain dicts.

Stage 1 ships the interface only (plus a demo-only mock used for seeding).
Future stages add RedditAdapter, YouTubeAdapter, XAdapter, ... without
touching the candidate system.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class NormalizedSource:
    """Platform-agnostic representation of a discovered post/thread."""

    platform: str
    source_id: str  # platform-native ID
    community: str | None = None
    author: str | None = None
    title: str | None = None
    content: str | None = None
    url: str | None = None
    posted_at: datetime | None = None
    engagement: dict[str, Any] = field(default_factory=dict)


class PlatformAdapter(ABC):
    """Contract every platform integration must satisfy."""

    #: Machine-readable platform identifier, e.g. "reddit".
    platform: str = "base"

    @abstractmethod
    def discover(
        self, query: str, limit: int = 25, **options: Any
    ) -> list[NormalizedSource]:
        """Find recent posts/threads relevant to `query`.

        `options` carries platform-specific search knobs (e.g. subreddit,
        sort, time_filter for Reddit). Adapters ignore options they do
        not understand. Core code passes these through opaquely — it must
        never import a platform's implementation to build them.
        """
        raise NotImplementedError

    @abstractmethod
    def fetch_post(self, source_id: str) -> NormalizedSource | None:
        """Fetch a single post by its platform-native ID."""
        raise NotImplementedError

    @abstractmethod
    def fetch_comments(self, source_id: str, limit: int = 50) -> list[dict[str, Any]]:
        """Fetch comments for a post as plain dicts (author, body, ...)."""
        raise NotImplementedError

    @abstractmethod
    def get_rules(self, community: str) -> dict[str, Any]:
        """Return community rules/guidelines as plain data."""
        raise NotImplementedError

    @abstractmethod
    def build_url(self, source_id: str) -> str:
        """Build the canonical public URL for a post."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Shared Reddit error vocabulary.
#
# These live here (not in a specific adapter) so every Reddit-facing layer —
# the Stage 2/3 application-level adapter and the Stage 4 user-OAuth flow —
# raises the same error types without services importing a platform
# implementation module. app/platforms/reddit.py re-exports them for
# backward compatibility.
# ---------------------------------------------------------------------------


class RedditError(Exception):
    """Base class for all Reddit API errors."""


class RedditCredentialsError(RedditError):
    """Credentials are missing or incomplete."""


class RedditAuthError(RedditError):
    """Authentication failed (bad client_id/secret, rejected token)."""


class RedditRateLimitError(RedditError):
    """Reddit returned HTTP 429. Back off; do NOT retry aggressively."""

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class RedditAPIError(RedditError):
    """Reddit returned an unexpected HTTP error."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class RedditNetworkError(RedditError):
    """Transport-level failure: timeout, DNS, connection reset, ..."""


class RedditResponseError(RedditError):
    """Reddit returned something we could not parse (malformed JSON/shape)."""
