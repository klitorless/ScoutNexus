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

## Current stage — Stage 1: Foundation

- Clean project structure (FastAPI + SQLite + SQLAlchemy + Jinja2)
- Domain models: **Campaign**, **Source**, **Candidate** (a source is a post;
  a candidate is the system's judgment that a source fits a campaign)
- Repository / data-access layer
- `PlatformAdapter` interface — core logic never touches platform APIs
- `DiscoveryService`: adapter → normalized sources → candidates
- Mobile-friendly web UI: opportunity inbox + candidate detail page
- Deterministic **mock** analysis (keyword-based, HIGH/MEDIUM/LOW confidence —
  no fake precision, no AI yet)
- Demo seed data (clearly fictional, no scraping, no real codes)
- Test suite

**Not in Stage 1:** no Reddit API connection, no Reddit credentials, no AI
classification, no automatic posting, no vote manipulation, no fake
identities, no rule/limit bypassing. Human approval stays in the loop by
design.

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
and architecture guarantees (the discovery pipeline runs against a stub
adapter with zero Reddit-specific code; no PRAW imports anywhere).

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

- **Stage 1** — Foundation + demo UI *(current)*
- **Stage 2** — Reddit API adapter
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
