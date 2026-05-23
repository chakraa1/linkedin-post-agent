# LinkedIn Post Agent

AI-powered pipeline that researches trending cloud/platform engineering topics,
writes a series of LinkedIn posts, validates them against 8 quality rules, then
lets you selectively approve and schedule each one to LinkedIn's native calendar.

Built on [CrewAI](https://github.com/joaomdmoura/crewai) with configurable LLM backends.

```
Competitor Analysis (9 senior leadership roles — Approach A or B)
    ↓
Research (Perplexity sonar-pro) → Research Validator
    ↓
Series Post Generation (Claude Sonnet — Problem / Insight / Outcome)
    ↓
Content Validator → 9-rule check (A-I) → auto-revise up to 2×
    ↓
Output to outputs/YYYY-MM-DD/ (date-based folders)
    ↓
Human review of .md files
    ↓
mark-reviewed → DB (draft → reviewed)
    ↓
approve-post (explicit gate) → LinkedIn Native Scheduler
    ↓
linkedin.com/content/scheduled/  (no daemon required)
```

---

## Quick Start

### 1. Install

```bash
cd F:\ClaudeRepo2\linkedin-post-agent
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS/Linux

pip install -r requirements.txt
```

### 2. Configure

```bash
copy .env.example .env
```

Edit `.env` and add your API keys:

| Variable | Where to get it |
|---|---|
| `ANTHROPIC_API_KEY` | https://console.anthropic.com |
| `PERPLEXITY_API_KEY` | https://www.perplexity.ai/settings/api |
| `OPENAI_API_KEY` | https://platform.openai.com (image generation, optional) |
| `LINKEDIN_CLIENT_ID` | https://developer.linkedin.com (see below) |
| `LINKEDIN_CLIENT_SECRET` | same as above |

### 3. LinkedIn App Setup (one-time)

1. Go to https://developer.linkedin.com → **Create app**
2. Under **Products**, request access to:
   - **Share on LinkedIn**
   - **Sign In with LinkedIn using OpenID Connect**
3. Under **Auth** → **Authorized redirect URLs**, add: `http://localhost:8080/callback`
4. Copy **Client ID** and **Client Secret** to your `.env`

### 4. Authenticate

```bash
python main.py auth
```

A browser window opens. Log in to LinkedIn and authorize. The token is saved
locally and added to `.env` for future runs.

### 5. First-time setup

```bash
python main.py setup
```

Configures calendar period (1 week / 1 month / 3 months), posting days, timezone,
series mode, and image generation provider.

### 6. Generate content

```bash
# Generate 1 week of posts (Mon/Wed/Fri series)
python main.py generate-calendar

# Full calendar at once, skip the Week-1 style preview
python main.py generate-calendar --full

# Pin all posts to one topic
python main.py generate-calendar --topic "eBPF in Kubernetes observability"
```

Posts are saved to `outputs/YYYY-MM-DD/` — review each `.md` file before approving.

### 7. Review and publish

```bash
# See all generated drafts
python main.py list-drafts

# Register a reviewed folder in the database
python main.py mark-reviewed outputs/2026-05-23/

# Approve and schedule to LinkedIn native calendar
python main.py approve-post outputs/2026-05-23/post_01_Mon_2026-05-25.md \
  --schedule "2026-05-26 12:30"

# Or post immediately
python main.py approve-post outputs/2026-05-23/post_01_Mon_2026-05-25.md --now

# See all tracked posts and their lifecycle state
python main.py list-posts

# Cancel a scheduled post (also cancel at linkedin.com/content/scheduled/)
python main.py cancel-post <post-id>
```

Scheduled posts appear at **linkedin.com/content/scheduled/** — no local daemon or
Windows Task Scheduler required.

---

## Switching LLMs

Edit `config/llm_config.yaml` to change which LLM powers each agent:

```yaml
agent_llm_mapping:
  research_agent:
    provider: perplexity
    model: sonar-pro

  content_writer:
    provider: anthropic
    model: claude-sonnet-4-6   # or claude-opus-4-7, gpt-4o, etc.
```

Validate your config:

```bash
python main.py config
python main.py config-show   # calendar / schedule config
```

---

## Agents

| Agent | Default LLM | Role |
|---|---|---|
| `research_agent` | Perplexity sonar-pro | Competitor analysis + trend research (Approach A/B) |
| `research_validator` | Claude Haiku 4.5 | Checks 4 sections, 3+ citations, differentiation angle |
| `content_writer` | Claude Sonnet 4.6 | Writes posts using COSTAR framework |
| `content_validator` | Claude Haiku 4.5 | 9-rule quality gate (A-I: tone, readability, words, hook, insight, guard, format, sources, hashtags, CTA) |
| `post_editor` | Claude Haiku 4.5 | Applies revision feedback |
| `edit_validator` | Claude Haiku 4.5 | Regression check after edits |
| `publisher` | Claude Haiku 4.5 | Confirms scheduling intent |

---

## Validation Rules

| Rule | What It Checks | Detection Method |
|---|---|---|
| A — Human Tone | Avg sentence ≤ 15 words + Flesch ≥ 60 + Haiku tone audit | Sentence parser + readability formula + LLM |
| B — Word Count | 200-300 words (hard reject > 300) | Automated counter |
| C — Eye-Catching Hook | Opening line specific, data-led, non-generic | Haiku LLM audit |
| D — Non-Obvious Insight | Contains at least one insight challenging conventional thinking | Haiku LLM-as-judge |
| E — Achievement Guard | No unhedged "I built / we implemented" — must be hedged or passive | Regex patterns |
| F — Format Compliance | Zero em/en dashes; use periods or commas instead | Unicode character scan |
| G — Source Links | ## SOURCES section with 2+ refs + HTTP 200 liveness check | URL extractor + HEAD request |
| H — Role Alignment | 3-5 hashtags, ≥1 matching senior leadership role whitelist | Whitelist validator |
| I — Engagement Hook | Post ends with a question or CTA inviting comments | End-of-post pattern check |

---

## Post Lifecycle

```
draft → reviewed → approved → scheduled → published
                             → failed
              → cancelled
```

Duplicate detection: SHA-256 hash of content stored in DB. The same post
cannot be scheduled or published twice (Step 7 dedup rule).

---

## Target Audience

Posts are crafted for senior leadership roles in investment banking and fintech:
- Senior VP / Director of Engineering
- Head of Cloud / Infrastructure / IT
- Senior Engineering Manager / Senior Architect
- CTO / CIO at investment banks and fintech firms

Topics: AI inference infrastructure, platform engineering, Kubernetes, FinOps,
cloud-native architecture, developer experience, observability, IaC.

---

## Project Structure

```
linkedin-post-agent/
├── main.py                          # CLI entry point (click)
├── run_calendar_headless.py         # Headless calendar generation script
├── config/
│   ├── agents.yaml                  # Agent role/goal/backstory (7 agents + 3 validators)
│   ├── tasks.yaml                   # Task descriptions and expected outputs
│   ├── llm_config.yaml              # LLM provider + per-agent model mapping
│   ├── calendar_config.yaml         # Calendar period, schedule, series mode
│   └── costar_config.yaml           # COSTAR persona, post format, validation config
├── src/linkedin_post_agent/
│   ├── crew.py                      # Pipeline orchestrator
│   ├── tools/
│   │   ├── perplexity_tool.py       # Perplexity web-search CrewAI tool
│   │   ├── linkedin_tool.py         # LinkedIn OAuth + native scheduler API
│   │   └── review_tool.py           # Interactive human review (Rich CLI)
│   └── utils/
│       ├── llm_factory.py           # Builds CrewAI LLM from YAML config
│       ├── post_validator.py        # 9-rule validation hook (A-I)
│       ├── scheduler.py             # DB state machine + LinkedIn posting
│       ├── calendar.py              # Content calendar generation
│       └── setup_wizard.py          # First-time setup wizard
├── docs/
│   └── WORKFLOW.md                  # Full 7-step workflow diagram (Mermaid)
└── outputs/
    ├── scheduler.db                 # Post lifecycle database (SQLite)
    └── YYYY-MM-DD/                  # Generated posts per run
        ├── post_01_Mon_*.md
        ├── post_02_Wed_*.md
        ├── post_03_Fri_*.md
        └── calendar_summary.md
```
