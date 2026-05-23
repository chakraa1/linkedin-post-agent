# LinkedIn Post Agent — Full Workflow (7 Steps)

## Pipeline Diagram

```mermaid
flowchart TD
    A([User / Config]) --> S1

    %% ── STEP 1: Competitor Analysis ──────────────────────────────────────────
    subgraph S1 ["STEP 1 — Competitor Analysis"]
        direction TB
        CA["Research Agent analyses peer profiles:\n• Senior VP / Director of Engineering\n• Head of Cloud / Infrastructure / IT\n• Senior Engineering Manager\n• Senior Architect (Cloud / Infra / Solutions)\n• Senior Consultant Infrastructure Engineering\n• CTO / CIO at investment banks & fintech"]
        CA --> AB{Approach?}
        AB -- "A: Derivative + Twist\n(topic is trending,\npeer posts exist)" --> APR["Find top peer post angle\nIdentify missing insight\nAdd practitioner twist"]
        AB -- "B: Fresh Research\n(breaking news,\nnew report)" --> BFR["Perplexity: past 7-14 days\nSurface angle peers\nhaven't covered yet"]
        APR --> RB["Research Brief\n• Top 3 topics + sources\n• Competitive landscape\n• Differentiation angle\n• Recommended approach"]
        BFR --> RB
    end

    RB --> RV

    subgraph RV_BOX ["Research Validator"]
        RV{"Sections present?\n3+ real citations?\nDifferentiation angle?\nConcrete data point?"}
        RV -- FAIL --> CA
        RV -- PASS --> S2
    end

    %% ── STEP 2: Sequential Post Generation ───────────────────────────────────
    subgraph S2 ["STEP 2 — Sequential Post Generation (Series Mode)"]
        direction TB
        SER["Research runs ONCE per topic\nContent Writer generates 2-3 posts:\n• Part 1: The Problem — provocative hook\n• Part 2: The Insight — non-obvious angle\n• Part 3: The Outcome — results + CTA"]
    end

    S2 --> S3

    %% ── STEP 3: Validation Hooks ─────────────────────────────────────────────
    subgraph S3 ["STEP 3 — Content Validation (9 Rules A-I)"]
        direction TB
        V1["A  Human tone: avg sentence ≤15 words + Flesch ≥60 + Haiku audit"]
        V2["B  200-300 words hard cap (hard reject &gt;300)"]
        V3["C  Eye-catching hook (Haiku LLM audit)"]
        V4["D  Non-obvious insight (Haiku LLM-as-judge)"]
        V5["E  Achievement guard: unhedged I/we claims blocked"]
        V6["F  Format compliance: no em/en dash"]
        V7["G  Source links + HTTP HEAD liveness check"]
        V8["H  3-5 role-targeted hashtags (whitelist)"]
        V9["I  Engagement hook: ends with question or CTA"]
        VR{"All 9 rules pass?"}
        V1 & V2 & V3 & V4 & V5 & V6 & V7 & V8 & V9 --> VR
        VR -- "FAIL\n(auto-revise\nup to 2x)" --> SER
        VR -- PASS --> S4
    end

    %% ── STEP 4: Output by Date ───────────────────────────────────────────────
    subgraph S4 ["STEP 4 — Output by Date Folder"]
        direction TB
        OUT["Saved to outputs/YYYY-MM-DD/\n• post_01_Mon_2026-05-26.md\n• post_02_Wed_2026-05-28.md\n• post_03_Fri_2026-05-30.md\n• calendar_summary.md\n\nMetadata per file:\n  words · hash · validation status"]
    end

    S4 --> S5

    %% ── STEP 5: Manual Review + DB State ─────────────────────────────────────
    subgraph S5 ["STEP 5 — Manual Review + Database State Machine"]
        direction TB
        MAN["User reviews .md files in IDE\nor any text editor"]
        MAN --> CMD["python main.py mark-reviewed outputs/2026-05-23/"]
        CMD --> DUP{Unique content?\nHash not in DB?}
        DUP -- "DUPLICATE\nRejected" --> SKIP(["Skipped"])
        DUP -- "UNIQUE\nRegistered" --> DB["DB status: reviewed\ndraft → reviewed → approved\n           ↓\n       scheduled\n           ↓\n       published"]
        DB --> SEL["User selects which posts\nto approve (selective)"]
    end

    SEL --> S6

    %% ── STEP 6: LinkedIn Native Scheduling ───────────────────────────────────
    subgraph S6 ["STEP 6 — Explicit Approval Gate + LinkedIn Posting"]
        direction TB
        GATE["python main.py approve-post <file>\n  --schedule 'YYYY-MM-DD 12:30'\n  --schedule 'YYYY-MM-DD 18:30'\n  --now"]
        GATE --> CONF{"Confirm:\n'Post to LinkedIn?'"}
        CONF -- "No" --> CANCEL(["Cancelled"])
        CONF -- "Yes" --> LI
        LI{"Now or Schedule?"}
        LI -- "Post Now" --> PUB["lifecycleState: PUBLISHED\nLive immediately"]
        LI -- "Schedule\n(12:30 or 18:30 IST)" --> SCHED["lifecycleState: SCHEDULED\nscheduledPublishTime: epoch_ms\nLinkedIn Native Scheduler"]
        PUB --> LIVE(["✅ Live on LinkedIn"])
        SCHED --> CAL(["📅 linkedin.com/content/scheduled\nEdit · Cancel · View in calendar"])
    end

    CAL & LIVE --> S7

    %% ── STEP 7: No Reposting ─────────────────────────────────────────────────
    subgraph S7 ["STEP 7 — Deduplication + No Reposting"]
        direction TB
        HASH["Content hash (SHA-256) stored in DB\nStatus → published / scheduled"]
        HASH --> CHECK{"Future attempt\nwith same hash?"}
        CHECK -- "BLOCKED\nDuplicate rejected" --> BLOCK(["❌ Cannot repost"])
        CHECK -- "New content" --> ALLOW(["✅ Proceeds normally"])
    end

    %% ── STYLES ───────────────────────────────────────────────────────────────
    style S1 fill:#0f2744,stroke:#4a9eff,color:#e0e0e0
    style RV_BOX fill:#0f2744,stroke:#4a9eff,color:#e0e0e0
    style S2 fill:#0f2744,stroke:#4a9eff,color:#e0e0e0
    style S3 fill:#1a2a1a,stroke:#4caf50,color:#e0e0e0
    style S4 fill:#1a1a2e,stroke:#7c6fff,color:#e0e0e0
    style S5 fill:#2a1a0f,stroke:#f5a623,color:#e0e0e0
    style S6 fill:#0d2818,stroke:#4caf50,color:#e0e0e0
    style S7 fill:#2a0f0f,stroke:#ef5350,color:#e0e0e0
```

---

## Step-by-Step Reference

| Step | What Happens | Command |
|------|-------------|---------|
| **1** | Competitor analysis of senior leadership roles (SVP/Director/Head of Cloud/Infra/IT/Architect) | Auto — runs during generation |
| **1A** | Approach A: take trending peer angle + add non-obvious practitioner twist | Config: `approach: A` |
| **1B** | Approach B: fresh Perplexity research — angle peers haven't covered | Config: `approach: B` |
| **2** | Series: research once, generate 3 connected posts (Problem → Insight → Outcome) | `series_mode.enabled: true` |
| **3** | 9 validation rules A-I: human tone (readability) · words · hook · non-obvious insight · achievement guard · format · source liveness · hashtags · engagement CTA | Auto — runs after each generation |
| **4** | Output saved to `outputs/YYYY-MM-DD/` with `calendar_summary.md` | Auto — runs during generation |
| **5** | Manual review of .md files → `mark-reviewed` registers in DB, dedup check | `python main.py mark-reviewed outputs/2026-05-23/` |
| **6** | Explicit approval gate → LinkedIn native scheduler (12:30 or 18:30 IST) | `python main.py approve-post <file> --schedule "2026-05-26 12:30"` |
| **7** | Content hash blocks reposting — same post cannot be scheduled or published twice | Automatic — enforced in `approve-post` |

---

## Validation Rules (Step 3 Detail)

| Rule | Issue Code | What It Checks | Detection Method |
|------|-----------|---------------|-----------------|
| A | `HUMAN_TONE_LENGTH` / `HUMAN_TONE_READABILITY` / `COMPLEXITY` / `GPT_TONE` | Avg sentence ≤ 15 words · Flesch ≥ 60 · Haiku tone audit | Sentence parser + readability formula + LLM |
| B | `TOO_SHORT` / `TOO_LONG` | 200-300 words (hard reject > 300) | Automated counter |
| C | `WEAK_HOOK` | Opening line specific, data-led, non-generic | Haiku LLM audit |
| D | `OBVIOUS_INSIGHT` | Contains non-obvious insight challenging conventional thinking | Haiku LLM-as-judge |
| E | `UNAUTHORIZED_CLAIM` | No unhedged "I built / we implemented / I've led" claims | Regex patterns |
| F | `FORMAT_COMPLIANCE` | Zero em/en dashes (— –) or double hyphen | Unicode character scan |
| G | `MISSING_SOURCES` / `DEAD_SOURCE_URL` | ## SOURCES section with 2+ refs + HTTP 200 liveness | URL extractor + HEAD request |
| H | `MISSING_HASHTAGS` / `WEAK_HASHTAGS` / `TOO_MANY_HASHTAGS` | 3-5 hashtags, ≥1 matching senior leadership role whitelist | Whitelist validator |
| I | `MISSING_ENGAGEMENT_HOOK` | Post ends with a question or CTA inviting comments | End-of-post pattern check |

---

## CLI Quick Reference

```bash
# Generate 1 week of content (series mode: 3 posts on 1 topic)
python main.py generate-calendar

# Generate full calendar (skip preview)
python main.py generate-calendar --full

# List all drafts by date
python main.py list-drafts

# Mark a folder as manually reviewed → register in DB
python main.py mark-reviewed outputs/2026-05-23/

# Approve and schedule (LinkedIn native calendar — visible at 12:30 or 18:30 IST)
python main.py approve-post outputs/2026-05-23/post_01_Mon_2026-05-26.md --schedule "2026-05-26 12:30"

# Approve and post now
python main.py approve-post outputs/2026-05-23/post_01_Mon_2026-05-26.md --now

# List all tracked posts (lifecycle states)
python main.py list-posts

# Cancel a scheduled post (also cancel at linkedin.com/content/scheduled/)
python main.py cancel-post <post-id>
```
