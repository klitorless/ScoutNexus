"""Discovery service: adapter -> normalized sources -> persisted sources.

Two separate concerns, kept explicit:

1. discover_sources() / run_discovery(): platform discovery -> stored
   Source rows. Needs no campaign. A Source is "a thing someone posted
   somewhere". Stage 3 adds DiscoveryTarget (configured searches) and
   run_discovery() (multi-target, deduplicated, error-isolated runs).

2. create_candidate_for_source(): evaluate one stored Source for one
   Campaign -> Candidate. A Candidate is "a source that may represent an
   opportunity for a particular campaign". Discovering a source must NOT
   inherently mean creating a candidate.

The deterministic keyword analysis below is Stage 1 scaffolding. A future
stage replaces it with real relevance analysis; the surrounding pipeline
does not change.

The service only talks to the PlatformAdapter interface — it knows nothing
about Reddit's API (or any platform's API).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.models import Campaign, Candidate, Confidence, Source
from app.platforms.base import NormalizedSource, PlatformAdapter
from app.repositories.candidate_repository import CandidateRepository
from app.repositories.source_repository import SourceRepository

# Deterministic keyword signals used by the Stage 1 mock analysis.
_HIGH_SIGNALS = (
    "looking for",
    "alternatives to",
    "recommend",
    "recommendation",
    "suggest",
    "anyone using",
    "what is everyone",
    "what's everyone",
)
_MEDIUM_SIGNALS = (
    "ai",
    "assistant",
    "coding",
    "developer",
    "tool",
    "invite",
    "referral",
)


def analyze_source_for_campaign(
    source: NormalizedSource, campaign: Campaign
) -> dict:
    """Deterministic mock analysis of a source for a campaign.

    Returns the reasoning fields for a Candidate. Pure function — no I/O,
    no network, no AI. Confidence is a coarse HIGH/MEDIUM/LOW bucket with
    the matched evidence spelled out, never a fake precise percentage.
    """
    text = f"{source.title or ''}\n{source.content or ''}".lower()
    high_hits = [s for s in _HIGH_SIGNALS if s in text]
    medium_hits = [s for s in _MEDIUM_SIGNALS if s in text]

    if high_hits:
        confidence = Confidence.HIGH
        reason = (
            "The author explicitly asks for recommendations or alternatives "
            f"({', '.join(high_hits)})."
        )
    elif medium_hits:
        confidence = Confidence.MEDIUM
        reason = (
            "The conversation is about a related topic "
            f"({', '.join(medium_hits[:3])}) but has no explicit ask."
        )
    else:
        confidence = Confidence.LOW
        reason = "Only a loose topical overlap; no clear signal of need."

    community = f"r/{source.community}" if source.community else source.platform
    return {
        "user_need": (
            f"Participants in {community} are discussing: "
            f"\"{source.title or '(untitled)'}\". "
            "They appear to be evaluating tools or looking for advice."
        ),
        "why_found": (
            f"Keyword match during discovery. {reason} "
            f"Evidence: title/content contained {high_hits + medium_hits[:3] or ['(none)']}."
        ),
        "potential_connection": (
            f"The '{campaign.name}' campaign ({campaign.offer_type} for "
            f"{campaign.service}) may be relevant to this conversation, since "
            "participants are actively comparing options. A human should judge "
            "fit before any response is drafted."
        ),
        "conversation_context": (source.content or "")[:400],
        "confidence": confidence,
        "evidence_quality": (
            f"{'Strong' if high_hits else 'Moderate' if medium_hits else 'Weak'}: "
            f"{len(high_hits)} explicit-ask signals, {len(medium_hits)} topical "
            "signals matched deterministically (Stage 1 mock analysis — no AI)."
        ),
    }


def source_to_normalized(source: Source) -> NormalizedSource:
    """Rebuild the platform-agnostic view of a persisted source."""
    return NormalizedSource(
        platform=source.platform,
        source_id=source.source_id,
        community=source.community,
        author=source.author,
        title=source.title,
        content=source.content,
        url=source.url,
        posted_at=source.posted_at,
        engagement=dict(source.engagement) if source.engagement else {},
    )


# ---------------------------------------------------------------------------
# Stage 3: controlled discovery targets and run results.
# ---------------------------------------------------------------------------


@dataclass
class DiscoveryTarget:
    """One configured search to run during a discovery pass.

    Deliberately generic: a query plus optional community, limit, sort and
    time filter. The adapter interprets the platform-specific knobs
    (e.g. Reddit maps community -> subreddit); core code passes them
    through opaquely and never imports platform implementations.
    """

    query: str
    community: str | None = None
    limit: int = 25
    sort: str = "new"
    time_filter: str = "week"
    enabled: bool = True
    platform: str = "reddit"

    def __post_init__(self) -> None:
        if not self.query or not self.query.strip():
            raise ValueError("DiscoveryTarget requires a non-empty query")
        if not isinstance(self.limit, int) or not 1 <= self.limit <= 100:
            raise ValueError(
                f"DiscoveryTarget limit must be an int between 1 and 100, "
                f"got {self.limit!r}"
            )
        for name in ("sort", "time_filter", "platform"):
            value = getattr(self, name)
            if not value or not str(value).strip():
                raise ValueError(f"DiscoveryTarget requires a non-empty {name}")
        # Note: the adapter itself validates sort/time_filter values
        # (e.g. Reddit rejects unknown sorts); such rejections surface as
        # per-target errors in DiscoveryResult, not as config exceptions.

    def describe(self) -> str:
        where = f" in {self.community}" if self.community else " (site-wide)"
        return f"query={self.query!r}{where} [{self.platform}]"


@dataclass
class DiscoveryResult:
    """Structured outcome of one run_discovery() pass.

    sources_seen counts every post returned by adapters (including repeats
    across targets); sources_created counts new Source rows; duplicates is
    the difference (seen but already stored). errors holds one entry per
    failed target: {"target": <description>, "error": "<Type: message>"}.
    """

    targets_attempted: int = 0
    targets_skipped: int = 0
    sources_seen: int = 0
    sources_created: int = 0
    duplicates: int = 0
    errors: list[dict[str, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


# Default targets used by the manual discovery console. Generic example
# searches — not campaign-specific opportunity rules.
DEFAULT_DISCOVERY_TARGETS: list[DiscoveryTarget] = [
    DiscoveryTarget(
        query="AI coding assistant",
        community="ClaudeCode",
        limit=10,
        sort="new",
        time_filter="week",
    ),
    DiscoveryTarget(
        query="looking for coding assistant",
        community="programming",
        limit=10,
        sort="new",
        time_filter="week",
    ),
]


class DiscoveryService:
    """Separates source discovery from campaign evaluation."""

    def __init__(
        self,
        adapter: PlatformAdapter,
        source_repo: SourceRepository,
        candidate_repo: CandidateRepository,
    ):
        self.adapter = adapter
        self.source_repo = source_repo
        self.candidate_repo = candidate_repo

    def _persist_source(self, normalized: NormalizedSource) -> tuple[Source, bool]:
        """Upsert one normalized post. Returns (source, created)."""
        return self.source_repo.upsert(
            platform=normalized.platform,
            source_id=normalized.source_id,
            community=normalized.community,
            author=normalized.author,
            title=normalized.title,
            content=normalized.content,
            url=normalized.url,
            posted_at=normalized.posted_at,
            engagement=normalized.engagement,
        )

    def discover_sources(
        self,
        queries: list[str],
        limit_per_query: int = 10,
    ) -> list[Source]:
        """Discover posts and persist them as Sources. No campaign required.

        Idempotent: re-running the same queries will not duplicate sources
        for the same (platform, source_id) pair.
        """
        sources: list[Source] = []
        seen_ids: set[int] = set()
        for query in queries:
            for normalized in self.adapter.discover(query, limit=limit_per_query):
                source, _ = self._persist_source(normalized)
                if source.id not in seen_ids:
                    seen_ids.add(source.id)
                    sources.append(source)
        return sources

    def run_discovery(
        self,
        targets: list[DiscoveryTarget],
    ) -> DiscoveryResult:
        """Run enabled discovery targets and persist discovered sources.

        Raw discovery only: never creates candidates, never runs campaign
        analysis, never needs a campaign. Idempotent per
        (platform, source_id) — the same post found by several targets or
        across repeated runs is stored once.

        Each target is isolated: one target's adapter failure is recorded
        in result.errors (never silently swallowed) while the other
        targets still run. The caller commits the session.
        """
        result = DiscoveryResult()
        for target in targets:
            if not target.enabled or target.platform != self.adapter.platform:
                result.targets_skipped += 1
                continue
            result.targets_attempted += 1
            try:
                discovered = self.adapter.discover(
                    target.query,
                    limit=target.limit,
                    subreddit=target.community,
                    sort=target.sort,
                    time_filter=target.time_filter,
                )
            except Exception as exc:
                result.errors.append(
                    {
                        "target": target.describe(),
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                continue
            for normalized in discovered:
                result.sources_seen += 1
                _, created = self._persist_source(normalized)
                if created:
                    result.sources_created += 1
                else:
                    result.duplicates += 1
        return result

    def create_candidate_for_source(
        self,
        source: Source,
        campaign: Campaign,
    ) -> Candidate | None:
        """Evaluate one stored source for one campaign.

        Returns the new Candidate, or None if this (campaign, source) pair
        already has one. Evaluation is a separate, explicit step — discovery
        alone never creates candidates.
        """
        if self.candidate_repo.get_for_campaign_source(campaign.id, source.id):
            return None
        analysis = analyze_source_for_campaign(
            source_to_normalized(source), campaign
        )
        return self.candidate_repo.create(
            campaign_id=campaign.id,
            source_id=source.id,
            **analysis,
        )

    def discover_for_campaign(
        self,
        campaign: Campaign,
        queries: list[str],
        limit_per_query: int = 10,
    ) -> list[Candidate]:
        """Convenience: discover sources, then evaluate each for the campaign.

        Idempotent: re-running the same queries creates nothing new.
        """
        candidates: list[Candidate] = []
        for source in self.discover_sources(queries, limit_per_query):
            candidate = self.create_candidate_for_source(source, campaign)
            if candidate is not None:
                candidates.append(candidate)
        return candidates
