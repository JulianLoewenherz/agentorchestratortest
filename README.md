# Competitive Intelligence Agent Fleet

A self-contained Python application demonstrating stateful multi-agent orchestration.
A **Planner** spawns 4 specialized scraping agents in parallel, each writing structured
findings to a shared JSON state store. An **Orchestrator** then uses Claude to synthesize
everything into a weekly competitive intelligence briefing.

## Architecture

```
competitors.yaml              # config: which competitors to track
│
├── run.py                    # entry point
│
├── agents/
│   ├── planner.py            # decomposes work, launches 4 agents in parallel
│   ├── changelog_agent.py    # scrapes /changelog and /releases pages
│   ├── jobs_agent.py         # scrapes job boards, infers hiring signals
│   ├── pricing_agent.py      # scrapes pricing pages, detects price changes
│   └── social_agent.py       # scrapes public Twitter/X via nitter mirrors
│
├── orchestrator.py           # reads shared_state/, calls Claude, writes briefing
│
└── shared_state/             # written at runtime (gitignored)
    ├── changelogs.json
    ├── job_postings.json
    ├── pricing.json
    ├── social_signals.json
    ├── run_manifest.json
    ├── weekly_briefing.md
    └── sample/               # mock data for testing without scraping
        ├── changelogs.json
        ├── job_postings.json
        ├── pricing.json
        └── social_signals.json
```

### Data Flow

```
competitors.yaml
      │
      ▼
  planner.py  ──ThreadPoolExecutor──►  changelog_agent.py ──► changelogs.json
                                    ►  jobs_agent.py      ──► job_postings.json
                                    ►  pricing_agent.py   ──► pricing.json
                                    ►  social_agent.py    ──► social_signals.json
                                              │
                                              ▼
                                       orchestrator.py
                                    (reads all 4 JSON files)
                                              │
                                    Claude claude-sonnet-4-6
                                              │
                                              ▼
                                    weekly_briefing.md + stdout
```

## Setup

```bash
# Install dependencies
pip install -r requirements.txt

# Set your Anthropic API key
export ANTHROPIC_API_KEY=sk-ant-...
```

## Usage

### Full run (scrape + synthesize)
```bash
python run.py
```

### Custom competitors config
```bash
python run.py --competitors my_competitors.yaml
```

### Re-synthesize from existing data (skip scraping)
```bash
python run.py --skip-scrape
```

### Test with sample data (no network calls, no API key needed for scraping)
```bash
python run.py --sample
```

### Run only one agent
```bash
python run.py --agent changelog
python run.py --agent jobs
python run.py --agent pricing
python run.py --agent social
```

### Scrape only (no Claude synthesis)
```bash
python run.py --no-briefing
```

## competitors.yaml format

```yaml
competitors:
  - name: Acme Corp
    website: https://acmecorp.com
    changelog_url: https://acmecorp.com/changelog
    pricing_url: https://acmecorp.com/pricing
    jobs_url: https://acmecorp.com/careers
    twitter_handle: acmecorp
```

All fields are optional — agents degrade gracefully if a URL is missing.

## Shared State Schema

Each agent writes a JSON file with this structure:

```json
{
  "agent": "changelog",
  "run_at": "2026-04-03T10:00:00Z",
  "competitors": {
    "CompetitorName": {
      "...": "agent-specific fields"
    }
  }
}
```

See `shared_state/sample/` for full examples of each schema.

## Agent Details

| Agent | Input | Output | Key signals |
|-------|-------|--------|-------------|
| `changelog` | `changelog_url` | `changelogs.json` | Latest entry, date, summary |
| `jobs` | `jobs_url` | `job_postings.json` | Dept breakdown, hiring signals |
| `pricing` | `pricing_url` | `pricing.json` | Plan tiers, price change detection |
| `social` | `twitter_handle` | `social_signals.json` | Recent posts, themes, sentiment |

## Requirements

- Python 3.10+
- `ANTHROPIC_API_KEY` environment variable (for the synthesis step only)
- Network access to competitor sites (for scraping step)

Social scraping uses [nitter](https://github.com/zedeus/nitter) public instances
(no Twitter API key required). Falls back gracefully if all instances are unavailable.
