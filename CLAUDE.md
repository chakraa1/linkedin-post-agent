# LinkedIn Post Agent — CLAUDE.md

## Project Overview

A multi-agent CrewAI pipeline that researches, writes, validates, and publishes LinkedIn posts with explicit human approval gates. No content reaches LinkedIn without running `python main.py approve-post`.

**Stack:** Python 3.11+, CrewAI, Anthropic Claude API, OpenAI, Perplexity, SQLite, LinkedIn API v2, Click CLI, Rich terminal UI.

## Architecture

### 7-Step Pipeline (`src/linkedin_post_agent/crew.py`)

1. **Research** — `research_agent` queries Perplexity sonar-pro or Claude Sonnet against 9 competitor profiles, returns a 600–1000 word structured brief with citations.
2. **Research Validation** — `research_validator` (Claude Haiku) enforces: 4 required sections, 3+ real citations, differentiation angle, concrete data points. PASS/FAIL gate.
3. **Content Writing** — `content_writer` (Claude Sonnet) generates post using the COSTAR framework defined in `config/costar_config.yaml`.
4. **Post Validation** — `PostValidator` runs 9 quality rules (A–I). Auto-revises up to 2× on failures before surfacing to human.
5. **Human Review** — Interactive Rich CLI review with feedback loop. Human edits trigger edit regression check.
6. **Edit Validation** — `edit_validator` runs regression check after human edits.
7. **Publish / Schedule** — Posts to LinkedIn immediately or schedules via LinkedIn native calendar API.

### Key Files

| File | Role |
|------|------|
| `main.py` | Click CLI — all user-facing commands |
| `src/linkedin_post_agent/crew.py` | Main orchestrator, 7-agent pipeline |
| `src/linkedin_post_agent/utils/post_validator.py` | 9-rule quality gate (A–I) |
| `src/linkedin_post_agent/utils/scheduler.py` | SQLite state machine for post lifecycle |
| `src/linkedin_post_agent/utils/calendar.py` | Batch content calendar generation |
| `src/linkedin_post_agent/utils/llm_factory.py` | Multi-provider LLM factory (reads YAML) |
| `src/linkedin_post_agent/tools/linkedin_tool.py` | OAuth 2.0 + LinkedIn API v2 |
| `src/linkedin_post_agent/tools/perplexity_tool.py` | Perplexity web research CrewAI tool |
| `src/linkedin_post_agent/tools/image_tool.py` | DALL-E 3 / Stable Diffusion image generation |
| `src/linkedin_post_agent/tools/review_tool.py` | Interactive human review (Rich CLI) |
| `src/linkedin_post_agent/utils/setup_wizard.py` | First-time interactive setup |
| `run_headless.py` | Headless research + generation → JSON output |
| `run_calendar_headless.py` | Headless batch calendar generation |
| `publish_post.py` | Standalone LinkedIn post publisher |

### Config Files (`config/`)

| File | Purpose |
|------|---------|
| `agents.yaml` | 7 agent definitions (role, goal, backstory) |
| `tasks.yaml` | CrewAI task descriptions and expected outputs |
| `llm_config.yaml` | Provider list + per-agent model mapping |
| `calendar_config.yaml` | Period, posting days, time/timezone, series mode |
| `costar_config.yaml` | COSTAR persona, writing constraints, audience, format rules |

**LLM assignments are in YAML, not hardcoded.** Switch models by editing `config/llm_config.yaml` or setting environment variable overrides (e.g. `CONTENT_AGENT_MODEL=gpt-4o`).

### Post Lifecycle (SQLite State Machine)

```
draft → reviewed → approved → scheduled → published
                                        ↘ failed / cancelled
```

Deduplication via SHA-256 hash — same content cannot be posted twice. Database lives at `outputs/scheduler.db`.

## Common Commands

### Setup
```bash
# Install dependencies
python -m venv .venv
.venv\Scripts\activate       # Windows
pip install -r requirements.txt

# Configure .env
copy .env.example .env
# Edit .env with API keys

# Authenticate with LinkedIn (opens browser OAuth flow)
python main.py auth

# Interactive calendar/schedule configuration
python main.py setup
```

### Generate Content
```bash
# Single post, interactive review
python main.py run --topic "Kubernetes cost optimization"

# Calendar — preview Week 1 Day 1 first
python main.py generate-calendar

# Continue calendar after approving preview
python main.py generate-calendar --continue-from 2

# Full calendar at once
python main.py generate-calendar --full

# Fixed topic for all posts
python main.py generate-calendar --topic "eBPF in production"
```

### Review & Approve
```bash
# List drafts
python main.py list-drafts

# Register a reviewed output folder in the DB
python main.py mark-reviewed outputs/2026-05-23/

# Approve and schedule to LinkedIn
python main.py approve-post outputs/2026-05-23/post_01_Mon_2026-05-25.md \
  --schedule "2026-05-26 12:30"

# Post immediately
python main.py approve-post outputs/2026-05-23/post_01_Mon_2026-05-25.md --now

# List all scheduled/published posts
python main.py list-posts

# Cancel a scheduled post
python main.py cancel-post <post-id>
```

### Config & Debug
```bash
python main.py config       # Show LLM config
python main.py config-show  # Show calendar/schedule settings
```

## Environment Variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `ANTHROPIC_API_KEY` | Yes | Claude API — research, content writing, validation |
| `OPENAI_API_KEY` | Yes | GPT-4o fallback + DALL-E 3 image generation |
| `PERPLEXITY_API_KEY` | Optional | sonar-pro web research (falls back to Claude Sonnet) |
| `LINKEDIN_CLIENT_ID` | Yes | OAuth app client ID |
| `LINKEDIN_CLIENT_SECRET` | Yes | OAuth app client secret |
| `LINKEDIN_ACCESS_TOKEN` | Auto | Populated by `python main.py auth` |
| `LINKEDIN_PERSON_URN` | Auto | Populated by `python main.py auth` |
| `IMAGE_PROVIDER` | Optional | `openai` \| `stability` \| `replicate` \| `none` |
| `PYTHONUTF8` | Windows | Set to `1` to fix UTF-8 encoding issues on Windows |
| `CONTENT_AGENT_MODEL` | Optional | Override content_writer model at runtime |

## Post Validation Rules (A–I)

Implemented in `src/linkedin_post_agent/utils/post_validator.py`:

- **A** — Human tone: avg sentence ≤15 words, Flesch readability ≥60
- **B** — Word count: 200–300 words (hard reject above 300)
- **C** — Eye-catching hook: LLM audit of opening line
- **D** — Non-obvious insight: LLM-as-judge check
- **E** — Achievement guard: no unhedged "I built / we implemented" claims
- **F** — Format compliance: no em dashes or en dashes
- **G** — Source links: `## SOURCES` section present + HTTP 200 liveness check
- **H** — Role alignment: 3–5 hashtags, ≥1 from leadership whitelist
- **I** — Engagement hook: post must end with a question or CTA

The validator auto-revises up to 2× before handing off to human review.

## COSTAR Persona

Defined in `config/costar_config.yaml`. The content_writer agents as a **Senior Platform Engineer, 20+ years experience**. Target audience: Cloud/Platform Engineers, CTOs, Directors of Engineering. Post format: 150–300 words, 3–5 hashtags, ends with a question.

Writing constraints: no em/en dashes, no corporate buzzwords, no unhedged achievement claims.

## Output Structure

```
outputs/
├── scheduler.db              # SQLite post lifecycle DB
└── YYYY-MM-DD/               # Posts organized by generation date
    ├── post_01_Mon_2026-05-25.md
    ├── post_02_Wed_2026-05-27.md
    ├── post_03_Fri_2026-05-29.md
    └── calendar_summary.md
```

Each `.md` file contains the post body, `## SOURCES`, and `## IMAGE_BRIEF` sections.

## Multi-Provider LLM Support

The `llm_factory.py` supports: **Anthropic, OpenAI, Perplexity, Ollama, Groq**. All assignments live in `config/llm_config.yaml` — no code changes needed to swap models. Per-agent overrides via environment variables.

Default mapping:
- `research_agent` → `anthropic/claude-sonnet-4-6`
- `content_writer` → `anthropic/claude-sonnet-4-6`
- `post_editor` → `anthropic/claude-haiku-4-5`
- `publisher` → `anthropic/claude-haiku-4-5`

## Important Design Constraints

- **No silent posting.** The `publisher` agent cannot post to LinkedIn directly. Publishing always requires explicit `python main.py approve-post` invocation.
- **Calendar preview-first.** By default, `generate-calendar` generates Week 1 Day 1 only. Run with `--full` or `--continue-from N` to generate more.
- **Series mode.** Calendar posts follow a Problem → Insight → Outcome rotation unless overridden via `--topic`.
- **SQLite deduplication.** SHA-256 content hash is stored; identical content will be rejected on re-submission.
- **Windows UTF-8.** Always set `PYTHONUTF8=1` in `.env` on Windows to avoid encoding errors in Rich terminal output.

## Tests

Minimal — `tests/__init__.py` only. The testable core modules are:
- `post_validator.py` — 9-rule logic
- `scheduler.py` — state machine transitions
- `llm_factory.py` — LLM initialization
- `calendar.py` — schedule generation

Run with: `pytest tests/`
