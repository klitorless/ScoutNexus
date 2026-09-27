"""Manual live Reddit smoke test (Stage 2).

Read-only: authenticates with app-only OAuth, runs one tiny search,
normalizes the results, prints safe metadata, and exits.

Requires Reddit credentials in the environment or a local .env file
(see .env.example). Fails gracefully when they are absent.

Usage:
    python scripts/test_reddit.py [--query "..."] [--subreddit ...] [--limit N]

Never posts, comments, votes, or modifies anything on Reddit.
Never prints credentials.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.platforms.reddit import (
    RedditAdapter,
    RedditCredentialsError,
    RedditError,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Reddit read-only smoke test.")
    parser.add_argument("--query", default="python", help="Search query.")
    parser.add_argument("--subreddit", default=None, help="Optional subreddit.")
    parser.add_argument("--limit", type=int, default=3, help="Max posts (1-5).")
    args = parser.parse_args()
    limit = max(1, min(args.limit, 5))

    try:
        adapter = RedditAdapter()
    except RedditCredentialsError as exc:
        print(f"SKIP: {exc}")
        return 2

    print(f"Query: {args.query!r}  subreddit: {args.subreddit or '(site-wide)'}")
    try:
        results = adapter.discover(
            args.query, limit=limit, subreddit=args.subreddit
        )
    except RedditError as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}")
        return 1

    print(f"Authenticated OK. Got {len(results)} normalized post(s).")
    for post in results:
        print("-" * 60)
        print(f"title:    {post.title}")
        print(f"sub:      r/{post.community}   author: u/{post.author}")
        print(f"posted:   {post.posted_at}   comments: {post.engagement.get('comments')}")
        print(f"url:      {post.url}")
    if adapter.rate_limit:
        print(f"rate-limit headers seen: {adapter.rate_limit}")
    print("Done — nothing was posted, voted, or modified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
