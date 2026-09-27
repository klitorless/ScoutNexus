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

## Current stage — Foundation reconciliation / architecture cleanup

The project was renamed from OpportunityScout to ScoutNexus, and the
foundation was reconciled against the intended architecture:

- **Source vs Candidate separation.** Platform discovery now persists
  `Source` rows without requiring a campaign
  (`DiscoveryService.discover_sources()`). Evaluating a stored source for
  a campaign (`DiscoveryService.create_candidate_for_source()`) is a
  separate, explicit step. Discovering a source never implies candidacy.
  `discover_for_campaign()` is a convenience that composes the two steps.
- **Deduplication.** Sources are unique on `(platform, source_id)` — the
  same post found by multiple queries is stored once. Candidates are
  unique per (campaign, source) pair, so one source can back candidates
  for many campaigns without duplicating the source.
- **Platform abstraction preserved.** Core services only talk to the
  `PlatformAdapter` interface; `NormalizedSource` is the boundary.
  No Reddit-specific imports outside `app/platforms/reddit.py`.
- **Web UI verified complete.** Mobile-first Jinja2 inbox with candidate
  list/detail, review/dismiss actions, campaign list, and static assets.
- **Naming.** User-facing name is now ScoutNexus. New configuration uses
  `SCOUTNEXUS_*` environment variables; legacy `OPPORTUNITYSCOUT_*`
  variables are still honored as a fallback (see Configuration).
- **Reddit adapter intact.** Read-only `RedditAdapter` behind
  `PlatformAdapter` (app-only OAuth2, no user password needed), with
  explicit error handling and fully mocked tests.

**Not implemented:** Stage 3 Reddit discovery/filtering, AI or semantic
relevance analysis, response drafting, automatic posting/commenting/
voting, scheduling, notifications, or additional platforms. The mock
keyword analysis from Stage 1 remains as clearly-labeled scaffolding.

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

## Tests

```bash
pytest -q
```

Covers: campaign/source/candidate persistence and relationships, the
NEW → REVIEWED / NEW → DISMISSED lifecycle, all routes (including 404s),
the Source/Candidate separation (discovery without a campaign,
cross-query deduplication, one source backing many campaigns),
Reddit adapter behavior (fully mocked — no credentials needed), and
architecture guarantees (the discovery pipeline runs against a stub
adapter with zero Reddit-specific code; core layers never import the
Reddit adapter).

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
  ScoutNexus rename *(done — current)*
- **Stage 3** — Reddit discovery / filtering *(next, not started)*
- **Stage 4** — AI relevance / intent analysis
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
