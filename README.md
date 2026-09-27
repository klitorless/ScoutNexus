# ScoutNexus

ScoutNexus is a **platform-independent opportunity discovery system**.

The idea: you provide an offer — an invite code, referral link, discount,
promo, beta invite, or similar campaign — and ScoutNexus finds existing
online conversations where that offer may reasonably be useful, presenting
them to you for **human review**. It is a research assistant, not an
automation tool: a human always decides what happens next.

```
Platforms
        ↓
PlatformAdapter
        ↓
NormalizedSource
        ↓
Source persistence
        ↓
Campaign evaluation
        ↓
Candidate (Campaign ↔ Source)
```

## Current stage — Stage 3: controlled Reddit discovery & source ingestion

Stage 3 builds a controlled, testable Reddit discovery layer on top of the
reconciled foundation:

```
DiscoveryTarget (query, community, limit, sort, time_filter, enabled)
        ↓
DiscoveryService.run_discovery()        # no campaign anywhere
        ↓
PlatformAdapter.discover()              # interface + platform options
        ↓
NormalizedSource                        # platform-agnostic post
        ↓
SourceRepository.upsert()               # dedupe on (platform, source_id)
        ↓
Source database records
```

- **Discovery targets.** A `DiscoveryTarget` configures one search:
  `query`, optional `community`, `limit`, `sort`, `time_filter`,
  `enabled`, `platform`. Targets are generic — no campaign-specific
  rules. The default set (`DEFAULT_DISCOVERY_TARGETS`) is a starting
  point; the same mechanism can later target software, discounts,
  referral offers, beta programs, etc.
- **Raw discovery, no campaigns.** `run_discovery()` persists `Source`
  rows and never creates `Candidate` rows, never calls
  `create_candidate_for_source()`, and never runs campaign analysis.
  It works with zero, one, or many campaigns.
- **Multi-target + dedup.** Enabled targets run in sequence through the
  same normalization/persistence path. The same Reddit post found by
  several targets (or by repeated runs) is stored once.
- **Error isolation.** One failing target is reported in
  `DiscoveryResult.errors` (with the actual exception type and message)
  while successful targets are preserved. Credentials are never logged.
- **Manual trigger.** `POST /discovery/run` runs the configured targets
  synchronously. There is no scheduling, no background workers, no
  recurring jobs. When Reddit credentials are absent, the page says so
  clearly instead of crashing.
- **Web verification.** `/discovery` shows the configured targets and a
  manual run button; `/sources` and `/sources/{id}` let you inspect
  discovered sources (platform, community, title, author, times,
  engagement, URL, source ID) — explicitly labeled as discovered
  sources, not candidates.
- **Platform independence.** The base `PlatformAdapter.discover()`
  signature now accepts `**options`; core code passes search knobs
  (subreddit, sort, time_filter) through opaquely. Only the Reddit
  adapter interprets them. Architecture tests enforce that
  `app/services/discovery.py` imports no Reddit implementation details.

**Not implemented:** AI or semantic relevance analysis, response
drafting, automatic posting/commenting/voting, scheduling, notifications,
additional platforms, or changes to the candidate review workflow.
The mock keyword analysis from Stage 1 remains as clearly-labeled
scaffolding in the candidate path only. Live Reddit discovery has not
been verified (no credentials were available during implementation).

## Setup

Requires Python 3.10+.

```bash
cd opportunityscout
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Load demo data (fictional posts + a demo "Muse Invite" campaign
# with placeholder code DEMO-MUSE-CODE — never a real code)
python scripts/seed_demo.py

# Run the web UI
python run.py
```

Open http://127.0.0.1:8000/candidates — the opportunity inbox.

Re-running `scripts/seed_demo.py` is idempotent; use `--reset` to wipe and
re-seed the demo data.

### Configuration

Environment variables (all optional). `SCOUTNEXUS_*` is preferred;
the original `OPPORTUNITYSCOUT_*` names still work as a fallback.

| Variable | Default | Purpose |
|---|---|---|
| `SCOUTNEXUS_DATABASE_URL` | `sqlite:///data/opportunityscout.db` | Database URL |
| `SCOUTNEXUS_HOST` | `127.0.0.1` | Bind host |
| `SCOUTNEXUS_PORT` | `8000` | Bind port |
| `SCOUTNEXUS_DEMO` | `true` | Show the "demo data" banner |

The default database filename keeps the original `opportunityscout.db`
name so existing local data is not orphaned by the rename.

## Routes

| Method | Route | Purpose |
|---|---|---|
| GET | `/` | Redirect to inbox |
| GET | `/candidates` | Opportunity inbox (`?status=new\|reviewed\|dismissed\|respond`) |
| GET | `/candidates/{id}` | Candidate detail |
| POST | `/candidates/{id}/review` | Mark REVIEWED |
| POST | `/candidates/{id}/dismiss` | Mark DISMISSED |
| GET | `/campaigns` | Campaign list |
| GET | `/discovery` | Discovery console (targets + manual run button) |
| POST | `/discovery/run` | Run discovery manually (dev operation) |
| GET | `/sources` | Discovered sources list |
| GET | `/sources/{id}` | Source detail |

## Discovery (Stage 3)

Discovery answers **"what Reddit sources did our configured searches
find?"** — it says nothing about whether a source is a good opportunity.
A discovered Reddit post becomes a `Source`. It becomes a `Candidate`
only later, when a human-driven campaign evaluation step runs.

### Running discovery manually

1. Configure Reddit credentials (see *Getting Reddit credentials* above —
   without them the run page reports that Reddit is not configured).
2. Review/adjust `DEFAULT_DISCOVERY_TARGETS` in
   `app/services/discovery.py`.
3. Open `/discovery` and click **Run discovery now**, or POST to
   `/discovery/run`.
4. Inspect the result summary and the persisted rows under `/sources`.

Deduplication is automatic: the same post returned by several targets
or by repeated runs is stored once, on `(platform, source_id)`.

### How it works

- `DiscoveryTarget` (`app/services/discovery.py`) — one configured
  search: `query`, optional `community`, `limit` (1–100), `sort`,
  `time_filter`, `enabled`, `platform`. Invalid configs raise
  `ValueError` at construction.
- `DiscoveryService.run_discovery(targets)` — runs enabled targets whose
  `platform` matches the adapter, persists each result through the
  existing upsert path, and returns a `DiscoveryResult`
  (`targets_attempted`, `targets_skipped`, `sources_seen`,
  `sources_created`, `duplicates`, `errors`). One target's adapter
  failure is recorded with its real exception and never hides
  authentication or rate-limit errors. The caller commits the session.
- The Reddit-specific search options stay inside the adapter:
  `RedditAdapter.discover()` maps `subreddit`/`sort`/`time_filter` to
  Reddit's search API; core code never imports Reddit internals.
  OAuth, token refresh, rate-limit tracking, bounded retries, and
  normalization behavior are unchanged from Stage 2.

## Tests

```bash
pytest -q
```

Covers: campaign/source/candidate persistence and relationships, the
NEW → REVIEWED / NEW → DISMISSED lifecycle, all routes (including 404s),
the Source/Candidate separation (discovery without a campaign,
cross-query deduplication, one source backing many campaigns),
discovery targets (config validation, enabled/disabled, per-target
options, dedup across targets and repeated runs, campaign independence,
error isolation — one failing target preserves the others), the web
discovery console and source inspection pages (including the clean
not-configured run), Reddit adapter behavior (fully mocked — no
credentials needed), and architecture guarantees (the discovery pipeline
runs against a stub adapter with zero Reddit-specific code; the service
module never imports the Reddit adapter).

## Reddit integration

A **read-only** Reddit platform adapter (`RedditAdapter`) lives behind
the `PlatformAdapter` interface. The discovery service, candidate system,
and database schema are platform-independent.

**ScoutNexus does not automatically post to Reddit.** No comments,
no votes, no messages — read-only by design.

### Getting Reddit credentials

Reddit's API requires OAuth2 even for public read access. ScoutNexus
uses the application-only `client_credentials` grant (no Reddit user
password needed):

1. Go to https://www.reddit.com/prefs/apps and click
   "are you a developer? create an app..."
2. Choose type **script** (a confidential client that runs on hardware you
   control, e.g. your laptop). Note your **client ID** (under the app name)
   and **client secret**.
3. Copy `.env.example` to `.env` and fill in:
   - `REDDIT_CLIENT_ID`
   - `REDDIT_CLIENT_SECRET`
   - `REDDIT_USER_AGENT` — Reddit *requires* a descriptive User-Agent in
     the form `<platform>:<app-id>:<version> (by /u/<your-username>)`.
     Generic agents get throttled or rejected.

`.env` is gitignored. Never commit real credentials.

### Running the live smoke test

```bash
python scripts/test_reddit.py [--query "..."] [--subreddit python] [--limit 3]
```

Authenticates, runs one small read-only search, prints safe metadata
(title, subreddit, author, URL — never credentials), and exits. It fails
gracefully with `SKIP` when credentials are not configured. The automated
test suite (`pytest`) never needs credentials.

### Reddit API notes (verified against official docs, 2026)

- Token endpoint: `POST https://www.reddit.com/api/v1/access_token`
  (HTTP Basic `client_id:client_secret`, `grant_type=client_credentials`).
- Data endpoints live at `https://oauth.reddit.com` with
  `Authorization: Bearer <token>` — not `www.reddit.com`.
- App-only tokens expire after ~1 hour (`expires_in`); there is no refresh
  token, so the adapter re-requests one automatically (with a 60s margin).
- Rate limit is ~60 requests/minute for OAuth. Responses include
  `X-Ratelimit-Used/Remaining/Reset` headers, which the adapter tracks;
  on HTTP 429 it raises a dedicated error instead of retrying.
- Search: `GET /search` (site-wide) or `GET /r/{subreddit}/search` with
  `restrict_sr=1`. Sorts: `relevance`, `new`, `hot`, `top`, `comments`.
  Time filters: `hour`, `day`, `week`, `month`, `year`, `all`.
- Post URLs are built as `https://www.reddit.com/comments/<id>/`, which
  Reddit redirects to the full permalink.
- Since 2025, Reddit's Responsible Builder Policy may require explicit
  approval before new API access is granted — if token requests fail with
  a fresh app, check your app's approval status. Unauthenticated public
  JSON endpoints are blocked; OAuth is the only supported path.

## Project structure

```
opportunityscout/
├── app/
│   ├── main.py                 # FastAPI app + routes
│   ├── core/                   # config, database setup
│   ├── models/                 # Campaign, Source, Candidate
│   ├── repositories/           # data-access layer
│   ├── services/               # DiscoveryService + mock analysis
│   ├── platforms/              # PlatformAdapter interface, mock + Reddit adapters
│   ├── templates/              # Jinja2 templates (mobile-friendly)
│   └── static/                 # vanilla CSS/JS
├── scripts/seed_demo.py        # demo data seeder
├── scripts/test_reddit.py      # live Reddit smoke test (optional credentials)
├── tests/                      # pytest suite
├── data/                       # SQLite database lives here (gitignored)
├── requirements.txt
└── run.py
```

## Roadmap (conceptual, may change)

- **Stage 1** — Foundation + demo UI *(done)*
- **Stage 2** — Reddit API adapter, read-only *(done)*
- **Reconciliation** — Source/Candidate separation, web layer repair,
  ScoutNexus rename *(done)*
- **Stage 3** — Controlled Reddit discovery / source ingestion *(done —
  current)*. Manual multi-target discovery through the existing
  `RedditAdapter`, deduplicated `(platform, source_id)` persistence,
  error-isolated runs, `/discovery` console and `/sources` inspection
  UI. No candidates, no campaign analysis, no scheduling. Live Reddit
  discovery was **not** verified (no credentials available).
- **Stage 4** — AI relevance / intent analysis *(next, not started)*
- **Stage 5** — Candidate review workflow
- **Stage 6** — Response drafting (human-approved)
- **Stage 7** — Additional platforms
- **Stage 8** — Offer discovery / search
- **Stage 9** — Optional platform publishing (human-approved)

## Product boundaries

ScoutNexus will not implement automatic mass posting, spam behavior,
fake identities, vote manipulation, or methods for bypassing subreddit
rules, rate limits, moderation, or platform restrictions. Human approval
remains part of any future response workflow.
