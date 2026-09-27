# OpportunityScout

OpportunityScout is a **platform-independent opportunity discovery system**.

The idea: you provide an offer — an invite code, referral link, discount,
promo, beta invite, or similar campaign — and OpportunityScout finds existing
online conversations where that offer may reasonably be useful, presenting
them to you for **human review**. It is a research assistant, not an
automation tool: a human always decides what happens next.

```
Campaigns / Offers
        ↓
OpportunityScout Core
        ↓
Platform Adapters
        ↓
Reddit · YouTube · X · Facebook · …
```

The first campaign is a Muse invite. The first platform is Reddit. Neither is
baked into the architecture — they are just the first entries.

## Current stage — Stage 2: Reddit adapter

- Everything from Stage 1, plus:
- `RedditAdapter`: read-only Reddit integration behind the existing
  `PlatformAdapter` interface (app-only OAuth2, no user password needed)
- Environment-based credentials (`.env`, gitignored; `.env.example`
  with placeholders)
- Reddit → `NormalizedSource` normalization (post ID, subreddit, author,
  title, selftext, permalink URL, timestamp, engagement metadata)
- Configurable search: query, subreddit (or site-wide), limit, sort,
  time filter
- Explicit error handling: auth failures, 429 rate limits (never
  aggressively retried), 5xx (bounded retry), timeouts, malformed
  responses — failures never become valid-looking candidates
- `python scripts/test_reddit.py`: manual live smoke test (graceful
  without credentials); automated tests are fully mocked
- UI shows a small "Reddit: Configured / Not configured" status line

**Not in Stage 2:** no AI analysis, no semantic relevance, no Muse-specific
logic in the adapter, no automatic posting/commenting/voting, no
scheduling. The adapter retrieves and normalizes — it does not judge
relevance.

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

Environment variables (all optional):

| Variable | Default | Purpose |
|---|---|---|
| `OPPORTUNITYSCOUT_DATABASE_URL` | `sqlite:///data/opportunityscout.db` | Database URL |
| `OPPORTUNITYSCOUT_HOST` | `127.0.0.1` | Bind host |
| `OPPORTUNITYSCOUT_PORT` | `8000` | Bind port |
| `OPPORTUNITYSCOUT_DEMO` | `true` | Show the "demo data" banner |

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
pytest -v
```

Covers: campaign/source/candidate persistence and relationships, the
NEW → REVIEWED / NEW → DISMISSED lifecycle, all routes (including 404s),
Reddit adapter behavior (fully mocked — no credentials needed), and
architecture guarantees (the discovery pipeline runs against a stub
adapter with zero Reddit-specific code; core layers never import the
Reddit adapter).

## Reddit integration (Stage 2)

Stage 2 adds a **read-only** Reddit platform adapter (`RedditAdapter`)
behind the existing `PlatformAdapter` interface. The discovery service,
candidate system, and database schema are unchanged.

**OpportunityScout does not automatically post to Reddit.** No comments,
no votes, no messages — read-only by design.

### Getting Reddit credentials

Reddit's API requires OAuth2 even for public read access. OpportunityScout
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
│   ├── platforms/              # PlatformAdapter interface, mock adapter
│   ├── templates/              # Jinja2 templates (mobile-friendly)
│   └── static/                 # vanilla CSS/JS
├── scripts/seed_demo.py        # demo data seeder
├── tests/                      # pytest suite
├── data/                       # SQLite database lives here (gitignored)
├── requirements.txt
└── run.py
```

## Roadmap (conceptual, may change)

- **Stage 1** — Foundation + demo UI
- **Stage 2** — Reddit API adapter *(current — read-only)*
- **Stage 3** — Reddit discovery / filtering
- **Stage 4** — AI relevance / intent analysis
- **Stage 5** — Candidate review workflow
- **Stage 6** — Response drafting (human-approved)
- **Stage 7** — Additional platforms
- **Stage 8** — Offer discovery / search
- **Stage 9** — Optional platform publishing (human-approved)

## Product boundaries

OpportunityScout will not implement automatic mass posting, spam behavior,
fake identities, vote manipulation, or methods for bypassing subreddit
rules, rate limits, moderation, or platform restrictions. Human approval
remains part of any future response workflow.
