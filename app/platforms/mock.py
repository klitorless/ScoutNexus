"""Demo-only mock adapter. NOT a real platform integration.

MockRedditAdapter implements the PlatformAdapter interface with canned,
clearly fictional posts so Stage 1 can demonstrate the full pipeline
(adapter -> normalized sources -> discovery service -> candidates) with
zero network access, zero credentials, and zero scraping.

It must never be mistaken for real Reddit data: every post it returns is
synthetic demo content.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from app.platforms import register_adapter
from app.platforms.base import NormalizedSource, PlatformAdapter


def _demo_posts() -> list[dict[str, Any]]:
    now = datetime.now()
    return [
        {
            "source_id": "demo_a1b2c3",
            "community": "AItools",
            "author": "demo_user_1",
            "title": "What AI coding tools are people using in 2026?",
            "content": (
                "I've been trying to find a solid AI coding assistant for my side "
                "projects. Tried a couple of autocomplete tools but they feel "
                "shallow. What is everyone actually using day to day? Looking for "
                "something that understands a whole repo, not just the current file."
            ),
            "posted_at": now - timedelta(hours=2),
            "engagement": {"comments": 34, "score": 128},
        },
        {
            "source_id": "demo_d4e5f6",
            "community": "SoloDev",
            "author": "demo_user_2",
            "title": "Looking for alternatives to Claude Code",
            "content": (
                "Claude Code has been great but I'm exploring what's else is out "
                "there before I commit. Any recommendations for AI dev tools with "
                "a generous free tier or invite system? Solo dev budget here."
            ),
            "posted_at": now - timedelta(hours=5),
            "engagement": {"comments": 21, "score": 87},
        },
        {
            "source_id": "demo_g7h8i9",
            "community": "learnprogramming",
            "author": "demo_user_3",
            "title": "What's everyone using for AI-assisted development?",
            "content": (
                "Beginner here. My bootcamp classmates keep talking about AI pair "
                "programmers. Which ones are actually worth learning vs hype? I "
                "don't want to become dependent on something I can't afford later."
            ),
            "posted_at": now - timedelta(hours=9),
            "engagement": {"comments": 58, "score": 203},
        },
        {
            "source_id": "demo_j1k2l3",
            "community": "SideProject",
            "author": "demo_user_4",
            "title": "Best AI tools for a solo developer?",
            "content": (
                "Shipping my first SaaS alone and drowning in boilerplate. If you "
                "could only keep one AI tool in your stack, what would it be and "
                "why? Bonus points if it has a referral or invite program."
            ),
            "posted_at": now - timedelta(days=1, hours=3),
            "engagement": {"comments": 12, "score": 45},
        },
        {
            "source_id": "demo_m4n5o6",
            "community": "productivity",
            "author": "demo_user_5",
            "title": "Is there an AI assistant that actually helps with code reviews?",
            "content": (
                "My team does async reviews and things slip through. Wondering if "
                "any AI assistant is genuinely good at reviewing PRs, not just "
                "writing code. Would love to hear real experiences."
            ),
            "posted_at": now - timedelta(days=2),
            "engagement": {"comments": 9, "score": 31},
        },
        {
            "source_id": "demo_p7q8r9",
            "community": "webdev",
            "author": "demo_user_6",
            "title": "Tired of boilerplate — what are people using to speed up?",
            "content": (
                "Every new project starts with the same boring setup. Curious what "
                "tools or workflows people use to skip the boilerplate phase. "
                "Open to trying new AI stuff if it's actually good."
            ),
            "posted_at": now - timedelta(days=3, hours=6),
            "engagement": {"comments": 44, "score": 156},
        },
    ]


@register_adapter
class MockRedditAdapter(PlatformAdapter):
    """In-memory demo adapter speaking the Reddit shape. No network."""

    platform = "reddit"
    is_demo = True

    def __init__(self) -> None:
        self._posts = _demo_posts()

    def discover(self, query: str, limit: int = 25) -> list[NormalizedSource]:
        tokens = [t for t in query.lower().split() if len(t) > 2]
        ranked: list[tuple[int, dict[str, Any]]] = []
        for post in self._posts:
            haystack = f"{post['title']} {post['content']}".lower()
            score = sum(haystack.count(t) for t in tokens)
            ranked.append((score, post))
        ranked.sort(key=lambda item: item[0], reverse=True)
        chosen = [post for score, post in ranked if score > 0] or [p for _, p in ranked]
        return [self._normalize(p) for p in chosen[:limit]]

    def fetch_post(self, source_id: str) -> NormalizedSource | None:
        for post in self._posts:
            if post["source_id"] == source_id:
                return self._normalize(post)
        return None

    def fetch_comments(self, source_id: str, limit: int = 50) -> list[dict[str, Any]]:
        # Stage 1 demo: no comment fetching.
        return []

    def get_rules(self, community: str) -> dict[str, Any]:
        return {
            "community": community,
            "demo": True,
            "note": "Demo rules — a real adapter returns the community's actual rules.",
            "rules": ["Be kind (demo rule).", "No spam (demo rule)."],
        }

    def build_url(self, source_id: str) -> str:
        post = next((p for p in self._posts if p["source_id"] == source_id), None)
        community = post["community"] if post else "unknown"
        return f"https://www.reddit.com/r/{community}/comments/{source_id}/demo/"

    def _normalize(self, post: dict[str, Any]) -> NormalizedSource:
        return NormalizedSource(
            platform=self.platform,
            source_id=post["source_id"],
            community=post["community"],
            author=post["author"],
            title=post["title"],
            content=post["content"],
            url=self.build_url(post["source_id"]),
            posted_at=post["posted_at"],
            engagement=dict(post["engagement"], demo=True),
        )
