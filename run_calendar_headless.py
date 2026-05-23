"""
Headless calendar runner — generates N posts for the configured period.
Supports series mode (2-3 sequential posts per topic) and date-based output folders.
Outputs CALENDAR_JSON: at the end for in-chat review.
"""
import json, os, sys, uuid, hashlib
from datetime import datetime, timedelta, date
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent / "src"))

import yaml
from crewai import Agent, Crew, Process, Task

PROJECT_ROOT    = Path(__file__).parent
CALENDAR_CONFIG = PROJECT_ROOT / "config" / "calendar_config.yaml"
COSTAR_CONFIG   = PROJECT_ROOT / "config" / "costar_config.yaml"

DAY_NAMES    = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
PERIOD_WEEKS = {"1_week": 1, "1_month": 4, "3_months": 13}

TOPIC_POOL = [
    "AI inference infrastructure on Kubernetes — cost and multi-tenancy",
    "OpenTelemetry production maturity and observability consolidation",
    "Platform engineering as a product — IDP adoption patterns",
    "FinOps for AI/ML workloads — controlling GPU compute spend",
    "Terraform vs OpenTofu — enterprise IaC fork decision",
    "Zero-trust security architecture in cloud-native environments",
    "Developer experience metrics — DORA + SPACE frameworks",
    "GitOps at scale — multi-cluster Flux / ArgoCD patterns",
    "eBPF in production — Kubernetes networking and security",
    "Service mesh consolidation — Cilium ambient vs Istio sidecar",
]

# Series angle prompts: each entry is (label, angle_instructions_template)
SERIES_ANGLES = {
    2: [
        (
            "The Challenge",
            "SERIES PART 1 of 2 — THE CHALLENGE\n\n"
            "Open with a bold, counterintuitive observation about {topic} in regulated/banking "
            "environments. Challenge a conventional assumption that peers repeat without question. "
            "Describe the core problem at scale with a specific detail only a practitioner would know. "
            "End with a question or unresolved tension that makes the reader want Part 2.",
        ),
        (
            "The Fix",
            "SERIES PART 2 of 2 — THE FIX\n\n"
            "This is the follow-up to Part 1 which raised a specific challenge about {topic}. "
            "Now reveal the non-obvious solution or insight. Be specific about the approach, "
            "the trade-offs accepted, and the concrete outcome achieved. Acknowledge the messiness. "
            "End with a clear lesson or strong call to action.",
        ),
    ],
    3: [
        (
            "The Problem",
            "SERIES PART 1 of 3 — THE PROBLEM\n\n"
            "Open with a bold, counterintuitive first line about {topic} that makes a senior leader "
            "stop scrolling. Challenge a common assumption. Be specific — name the constraint, "
            "the tool that fails, or the organisational reality that vendors ignore. "
            "End with a cliffhanger question that sets up Part 2.",
        ),
        (
            "The Insight",
            "SERIES PART 2 of 3 — THE INSIGHT\n\n"
            "This follows Part 1 which raised a specific challenge about {topic}. "
            "Reveal the non-obvious insight that most practitioners miss. Go deeper than "
            "surface analysis — include a specific data point, counterintuitive finding, "
            "or practitioner observation. Reference the tension raised in Part 1. "
            "Do not resolve everything — leave the question of outcomes for Part 3.",
        ),
        (
            "The Outcome",
            "SERIES PART 3 of 3 — THE OUTCOME\n\n"
            "This concludes the series on {topic} (Parts 1 and 2 set up the problem and insight). "
            "Share what actually happened: a concrete result, a metric, a decision that was made. "
            "Be honest about trade-offs and what was learned. Tie back to the challenge from Part 1. "
            "End with a strong call to action or reflection that invites comments.",
        ),
    ],
    5: [
        (
            "The Industry Pattern",
            "SERIES PART 1 of 5 — THE INDUSTRY PATTERN\n\n"
            "Open with a counterintuitive observation about {topic} that your peer competitors are "
            "overlooking or mischaracterising. Name the specific pattern you have observed across "
            "multiple organisations. Use a concrete data point or metric to anchor it. "
            "Do NOT explain why or offer solutions yet — leave that unresolved. "
            "End with a sharp question that makes a senior engineering leader stop and think.",
        ),
        (
            "The Competitive Blind Spot",
            "SERIES PART 2 of 5 — THE COMPETITIVE BLIND SPOT\n\n"
            "This follows Part 1 which named the pattern in {topic}. "
            "Now reveal what most peer posts and vendor narratives are getting wrong about this topic. "
            "Name the common framing that is incomplete or misleading. "
            "Ground it in an organisational or regulatory constraint that practitioners hit "
            "but thought-leadership articles ignore. Do not jump to solutions. "
            "End with a tension that sets up Part 3.",
        ),
        (
            "The Non-Obvious Insight",
            "SERIES PART 3 of 5 — THE NON-OBVIOUS INSIGHT\n\n"
            "This is the analytical core of the series on {topic} (Parts 1 and 2 named the pattern "
            "and blind spot). Now surface the single most counterintuitive insight that separates "
            "practitioners from commentators. This is not a solution — it is a deeper understanding "
            "of the root constraint. Include one specific data point or case detail. "
            "End by signalling that Part 4 will show what actually works.",
        ),
        (
            "The Practitioner Framework",
            "SERIES PART 4 of 5 — THE PRACTITIONER FRAMEWORK\n\n"
            "Building on Parts 1-3 which diagnosed the real problem in {topic}, now share the "
            "specific approach that actually works in regulated environments. Be concrete: name "
            "the decision sequence, the trade-off accepted, the thing you had to stop doing first. "
            "This is the how — grounded in real implementation experience, not vendor documentation. "
            "Do not wrap up the series yet — end by framing what the outcomes actually look like.",
        ),
        (
            "The Honest Outcome",
            "SERIES PART 5 of 5 — THE HONEST OUTCOME\n\n"
            "This concludes the week-long series on {topic}. "
            "Share what actually changed — a metric, a cost number, a decision that was reversed. "
            "Be honest about what worked and what surprised you. Acknowledge the parts that "
            "are still unresolved. Tie back to the counterintuitive pattern named on Monday. "
            "End with a strong, specific question that invites senior leaders to share their "
            "own experience and drives comments.",
        ),
    ],
}


def load_yaml(path):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_costar_prompt(cfg: dict, series_angle: str = "") -> str:
    c   = cfg["costar"]
    ctx = c["context"]
    obj = c["objective"]
    sty = c["style"]
    tone = c["tone"]
    aud  = c["audience"]
    fmt  = c["format"]

    secondary      = "\n".join(f"  - {s}" for s in obj.get("secondary", []))
    audience       = ", ".join(aud.get("primary", []))
    misc           = "\n".join(f"  - {m}" for m in tone.get("signals", []))
    hashtags       = fmt.get("hashtags", "3-5 targeted hashtags matching senior leadership roles")
    avoid_list     = "\n".join(f"  - {a}" for a in sty.get("avoid", []))
    voice_guidance = sty.get("voice_guidance", sty.get("voice", "Professional but conversational"))

    series_block = f"\n\nSERIES ANGLE TO FOLLOW:\n{series_angle}\n" if series_angle else ""

    return f"""You are writing a LinkedIn post using the COSTAR framework for this persona:

C – CONTEXT:
Role: {ctx['role']}
Background: {ctx['experience']}
Career goal: {ctx['career_goal']}

O – OBJECTIVE:
Primary: {obj['primary']}
Secondary goals:
{secondary}

S – STYLE:
{voice_guidance}

Metaphor: {sty.get('metaphor', '')}
Orientation: {sty['orientation']}

STRICTLY AVOID:
{avoid_list}

T – TONE:
{tone['primary']}
Angle: {tone['angle']}
Must signal:
{misc}

A – AUDIENCE:
{audience}
They are: {', '.join(aud.get('characteristics', []))}
Desired actions: {', '.join(aud.get('desired_actions', []))}

R – RESPONSE FORMAT:
- Length: {fmt['word_count']}
- Paragraphs: {fmt['paragraph_length']}
- Opening: {fmt['structure']['opening']}
- Body: {fmt['structure']['body']}
- Closing: {fmt['structure']['closing']}
- Hashtags: {hashtags} — must include at least one from: #CloudEngineering #PlatformEngineering #EngineeringLeadership #CloudArchitecture #TechLeadership
- No emoji. No bullet lists.
{series_block}
CRITICAL WRITING RULES (non-negotiable):
1. NEVER use an em dash (—). Replace with a comma, colon, or new sentence.
2. NEVER use corporate buzzwords: leverage, synergies, utilize, robust, seamless, cutting-edge, transformative, empower.
3. ALWAYS tie the insight to a concrete business outcome or ROI. Numbers beat adjectives.
4. INCLUDE at least one specific, real example with a concrete outcome (metric, timeline, decision). Anonymize the organisation (e.g. "a major trading platform", "a Tier 1 investment bank").
5. Draw from the research provided. Use real data points and recent trends.
6. Adopt the voice of someone who has operated these systems at scale in a regulated environment.
7. Challenge a common misconception or vendor promise head-on.
8. Surface a non-obvious insight that only a practitioner with scale experience would have.
9. Use the competitive differentiation angle from research (## Competitive Landscape) if present.
10. Keep it 200-300 words. Hard cap at 300.

After the post, output TWO additional sections using these exact headers:

## SOURCES
List 2-3 validated, authoritative references (real domains + article/report title).
Format: [Title] - domain.com
(No em dashes in sources either.)

## IMAGE_BRIEF
Describe a diagram or visual (80 words max) that would make this post shareable.
Type of visual, key elements, and the single insight it communicates at a glance.
"""


def build_schedule(cal_cfg: dict) -> list:
    period   = cal_cfg.get("period", "1_week")
    days     = cal_cfg.get("posting_days", [0, 2, 4])
    sched    = cal_cfg.get("schedule", {"time": "17:30", "timezone": "Asia/Kolkata"})
    weeks    = PERIOD_WEEKS.get(period, 1)
    today    = date.today()
    start    = today + timedelta(days=(7 - today.weekday()) % 7 or 7)
    time_h, time_m = map(int, sched["time"].split(":"))

    slots, slot_num = [], 1
    current = start
    end     = start + timedelta(weeks=weeks)

    while current < end:
        if current.weekday() in days:
            dt = datetime(current.year, current.month, current.day, time_h, time_m)
            slots.append({
                "slot":         slot_num,
                "date":         current.isoformat(),
                "weekday":      DAY_NAMES[current.weekday()],
                "datetime_str": dt.strftime("%Y-%m-%d %H:%M"),
                "timezone":     sched.get("timezone", "UTC"),
            })
            slot_num += 1
        current += timedelta(days=1)

    return slots


def parse_output(raw: str):
    sources_marker = "## SOURCES"
    image_marker   = "## IMAGE_BRIEF"
    post = raw
    sources = ""
    image_brief = ""

    if sources_marker in raw:
        parts = raw.split(sources_marker, 1)
        post = parts[0].strip()
        rest = parts[1]
        if image_marker in rest:
            src_parts = rest.split(image_marker, 1)
            sources = src_parts[0].strip()
            image_brief = src_parts[1].strip()
        else:
            sources = rest.strip()
    elif image_marker in raw:
        parts = raw.split(image_marker, 1)
        post = parts[0].strip()
        image_brief = parts[1].strip()

    return post, sources, image_brief


def validate_research(factory, agents_cfg, tasks_cfg, research: str) -> bool:
    agent = Agent(
        role=agents_cfg["research_validator"]["role"],
        goal=agents_cfg["research_validator"]["goal"],
        backstory=agents_cfg["research_validator"]["backstory"],
        llm=factory.get_llm("research_agent"),
        verbose=False, allow_delegation=False, max_iter=1,
    )
    desc = tasks_cfg["validate_research_task"]["description"].replace(
        "{research_output}", research
    )
    task = Task(
        description=desc,
        expected_output=tasks_cfg["validate_research_task"]["expected_output"],
        agent=agent,
    )
    crew = Crew(agents=[agent], tasks=[task], process=Process.sequential, verbose=False)
    result = str(crew.kickoff()).strip()
    return "VERDICT: FAIL" not in result and "VERDICT:FAIL" not in result


def run_research(factory, agents_cfg, tasks_cfg, topic: str) -> str:
    from linkedin_post_agent.tools.perplexity_tool import PerplexityResearchTool
    tools = [PerplexityResearchTool()] if os.getenv("PERPLEXITY_API_KEY") else []
    agent = Agent(
        role=agents_cfg["research_agent"]["role"],
        goal=agents_cfg["research_agent"]["goal"],
        backstory=agents_cfg["research_agent"]["backstory"],
        tools=tools,
        llm=factory.get_llm("research_agent"),
        verbose=False, allow_delegation=False, max_iter=2,
    )
    task = Task(
        description=tasks_cfg["research_task"]["description"] + f"\n\nFocus specifically on: {topic}",
        expected_output=tasks_cfg["research_task"]["expected_output"],
        agent=agent,
    )
    crew = Crew(agents=[agent], tasks=[task], process=Process.sequential, verbose=False)
    return str(crew.kickoff()).strip()


def run_content(factory, agents_cfg, research: str, costar: str) -> tuple:
    agent = Agent(
        role=agents_cfg["content_writer"]["role"],
        goal=agents_cfg["content_writer"]["goal"],
        backstory=agents_cfg["content_writer"]["backstory"],
        llm=factory.get_llm("content_writer"),
        verbose=False, allow_delegation=False, max_iter=2,
    )
    description = f"""{costar}

Research to draw from:
{research}

Write the LinkedIn post now, then append ## SOURCES and ## IMAGE_BRIEF."""
    task = Task(
        description=description,
        expected_output="LinkedIn post (200-300 words) + ## SOURCES + ## IMAGE_BRIEF",
        agent=agent,
    )
    crew = Crew(agents=[agent], tasks=[task], process=Process.sequential, verbose=False)
    return parse_output(str(crew.kickoff()).strip())


def run_revision(factory, agents_cfg, research: str, costar: str, previous_post: str, feedback: str) -> tuple:
    agent = Agent(
        role=agents_cfg["content_writer"]["role"],
        goal=agents_cfg["content_writer"]["goal"],
        backstory=agents_cfg["content_writer"]["backstory"],
        llm=factory.get_llm("content_writer"),
        verbose=False, allow_delegation=False, max_iter=2,
    )
    description = (
        f"REVISION REQUEST\n\n"
        f"{feedback}\n\n"
        f"Previous post:\n{previous_post}\n\n"
        f"{costar}\n\n"
        f"Research:\n{research}\n\n"
        "Output the revised post followed by ## SOURCES and ## IMAGE_BRIEF."
    )
    task = Task(
        description=description,
        expected_output="Revised LinkedIn post (200-300 words) + ## SOURCES + ## IMAGE_BRIEF with all issues fixed.",
        agent=agent,
    )
    crew = Crew(agents=[agent], tasks=[task], process=Process.sequential, verbose=False)
    return parse_output(str(crew.kickoff()).strip())


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-slots", type=int, default=None)
    parser.add_argument("--start-slot", type=int, default=1)
    args = parser.parse_args()

    cfg      = load_yaml(CALENDAR_CONFIG)
    cal_cfg  = cfg.get("calendar", {})
    costar_raw = load_yaml(COSTAR_CONFIG) if COSTAR_CONFIG.exists() else {}

    # Series mode config
    series_cfg         = cal_cfg.get("series_mode", {})
    series_enabled     = series_cfg.get("enabled", False)
    posts_per_series   = series_cfg.get("posts_per_series", 3)
    if posts_per_series not in (2, 3, 5):
        posts_per_series = 3

    preview_first = cal_cfg.get("preview_first", True)
    if args.max_slots is not None:
        max_slots = args.max_slots
    elif preview_first and args.start_slot == 1:
        max_slots = 1
    else:
        max_slots = None

    from linkedin_post_agent.utils.llm_factory import LLMFactory
    from linkedin_post_agent.utils.post_validator import PostValidator
    factory    = LLMFactory(str(PROJECT_ROOT / "config" / "llm_config.yaml"))
    agents_cfg = load_yaml(PROJECT_ROOT / "config" / "agents.yaml")
    tasks_cfg  = load_yaml(PROJECT_ROOT / "config" / "tasks.yaml")
    validator  = PostValidator()

    full_schedule = build_schedule(cal_cfg)
    schedule = [s for s in full_schedule if s["slot"] >= args.start_slot]
    if max_slots is not None:
        schedule = schedule[:max_slots]

    total_in_calendar = len(full_schedule)
    print(f"CALENDAR_SLOTS:{total_in_calendar}", flush=True)
    print(f"GENERATING_SLOTS:{len(schedule)}", flush=True)
    if preview_first and max_slots == 1 and args.start_slot == 1:
        print("PREVIEW_MODE:true", flush=True)
    if series_enabled:
        print(f"SERIES_MODE:{posts_per_series}_posts_per_topic", flush=True)

    # Date-based output folder (Step 4)
    gen_date  = datetime.now().strftime("%Y-%m-%d")
    posts_dir = PROJECT_ROOT / "outputs" / gen_date
    posts_dir.mkdir(parents=True, exist_ok=True)

    # Track per-slot metadata for summary
    summary_rows = []
    posts = []

    # Series mode: group slots by topic (all slots in a run cover one topic in a series)
    if series_enabled and len(schedule) > 1:
        # Pick ONE topic for the entire series
        topic = TOPIC_POOL[(schedule[0]["slot"] - 1) % len(TOPIC_POOL)]
        angles = SERIES_ANGLES[min(posts_per_series, len(schedule))]

        print(f"SERIES_TOPIC:{topic}", flush=True)

        # Research ONCE for the whole series
        print(f"PHASE:research:series", flush=True)
        research = run_research(factory, agents_cfg, tasks_cfg, topic)

        print(f"PHASE:validate_research:series", flush=True)
        rv_passed = validate_research(factory, agents_cfg, tasks_cfg, research)
        print(f"RESEARCH_VALIDATION_{'PASS' if rv_passed else 'FAIL'}:series", flush=True)

        for i, slot in enumerate(schedule):
            angle_idx = i % len(angles)
            angle_label, angle_instructions = angles[angle_idx]
            angle_text = angle_instructions.format(topic=topic)

            costar = build_costar_prompt(costar_raw, series_angle=angle_text)

            print(f"SLOT_START:{slot['slot']}:{topic} — {angle_label}", flush=True)
            print(f"PHASE:content:{slot['slot']}", flush=True)

            post_text, sources, image_brief = run_content(factory, agents_cfg, research, costar)

            # Validation + auto-revision
            for attempt in range(validator.MAX_RETRIES):
                vr = validator.validate(post_text, sources)
                if vr.passed:
                    break
                print(f"VALIDATION_FAIL:{slot['slot']}:attempt={attempt+1}:{'; '.join(vr.issues[:2])}", flush=True)
                post_text, sources, image_brief = run_revision(
                    factory, agents_cfg, research, costar,
                    post_text, vr.as_revision_feedback(),
                )
            vr_final = validator.validate(post_text, sources)
            if not vr_final.passed:
                print(f"VALIDATION_WARN:{slot['slot']}:{'; '.join(vr_final.issues)}", flush=True)

            word_count = len(post_text.split())
            post_data = {
                "id":            uuid.uuid4().hex[:8],
                "content_hash":  content_hash(post_text),
                "slot":          slot["slot"],
                "date":          slot["date"],
                "weekday":       slot["weekday"],
                "schedule_time": slot["datetime_str"],
                "timezone":      slot["timezone"],
                "topic":         topic,
                "series_part":   angle_label,
                "post":          post_text,
                "sources":       sources,
                "image_brief":   image_brief,
                "word_count":    word_count,
                "status":        "draft",
                "validation":    {"passed": vr_final.passed, "issues": vr_final.issues},
            }
            posts.append(post_data)

            # Save individual post .md file with date folder (Step 4)
            md_path = posts_dir / f"post_{slot['slot']:02d}_{slot['weekday'][:3]}_{slot['date']}.md"
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(f"# Post {slot['slot']} — {slot['weekday']} {slot['date']} ({angle_label})\n\n")
                f.write(f"**Topic:** {topic}\n")
                f.write(f"**Series:** {angle_label}\n")
                f.write(f"**Schedule:** {slot['datetime_str']} {slot['timezone']}\n")
                f.write(f"**Words:** {word_count}\n")
                f.write(f"**Hash:** {post_data['content_hash']}\n")
                f.write(f"**Validation:** {'✅ PASS' if vr_final.passed else '⚠️ WARN — ' + '; '.join(vr_final.issues[:1])}\n\n")
                f.write(f"## Validation Report\n\n")
                f.write(vr_final.validation_table())
                f.write("\n\n")
                f.write(f"## Post\n\n{post_text}\n\n")
                if sources:
                    f.write(f"## Sources\n\n{sources}\n\n")
                if image_brief:
                    f.write(f"## Image Brief\n\n{image_brief}\n\n")

            val_icon = "✅ PASS" if vr_final.passed else "⚠️ WARN"
            summary_rows.append((slot["slot"], md_path.name, slot["datetime_str"], angle_label, word_count, val_icon))
            print(f"SLOT_DONE:{slot['slot']}", flush=True)

    else:
        # Standard mode: one post per topic, different topics per slot
        for slot in schedule:
            topic = TOPIC_POOL[(slot["slot"] - 1) % len(TOPIC_POOL)]
            costar = build_costar_prompt(costar_raw)

            print(f"SLOT_START:{slot['slot']}:{topic}", flush=True)
            print(f"PHASE:research:{slot['slot']}", flush=True)
            research = run_research(factory, agents_cfg, tasks_cfg, topic)

            print(f"PHASE:validate_research:{slot['slot']}", flush=True)
            rv_passed = validate_research(factory, agents_cfg, tasks_cfg, research)
            print(f"RESEARCH_VALIDATION_{'PASS' if rv_passed else 'FAIL'}:{slot['slot']}", flush=True)

            print(f"PHASE:content:{slot['slot']}", flush=True)
            post_text, sources, image_brief = run_content(factory, agents_cfg, research, costar)

            for attempt in range(validator.MAX_RETRIES):
                vr = validator.validate(post_text, sources)
                if vr.passed:
                    break
                print(f"VALIDATION_FAIL:{slot['slot']}:attempt={attempt+1}:{'; '.join(vr.issues[:2])}", flush=True)
                post_text, sources, image_brief = run_revision(
                    factory, agents_cfg, research, costar,
                    post_text, vr.as_revision_feedback(),
                )
            vr_final = validator.validate(post_text, sources)
            if not vr_final.passed:
                print(f"VALIDATION_WARN:{slot['slot']}:{'; '.join(vr_final.issues)}", flush=True)

            word_count = len(post_text.split())
            post_data = {
                "id":            uuid.uuid4().hex[:8],
                "content_hash":  content_hash(post_text),
                "slot":          slot["slot"],
                "date":          slot["date"],
                "weekday":       slot["weekday"],
                "schedule_time": slot["datetime_str"],
                "timezone":      slot["timezone"],
                "topic":         topic,
                "series_part":   None,
                "post":          post_text,
                "sources":       sources,
                "image_brief":   image_brief,
                "word_count":    word_count,
                "status":        "draft",
                "validation":    {"passed": vr_final.passed, "issues": vr_final.issues},
            }
            posts.append(post_data)

            md_path = posts_dir / f"post_{slot['slot']:02d}_{slot['weekday'][:3]}_{slot['date']}.md"
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(f"# Post {slot['slot']} — {slot['weekday']} {slot['date']}\n\n")
                f.write(f"**Topic:** {topic}\n")
                f.write(f"**Schedule:** {slot['datetime_str']} {slot['timezone']}\n")
                f.write(f"**Words:** {word_count}\n")
                f.write(f"**Hash:** {post_data['content_hash']}\n")
                f.write(f"**Validation:** {'✅ PASS' if vr_final.passed else '⚠️ WARN — ' + '; '.join(vr_final.issues[:1])}\n\n")
                f.write(f"## Validation Report\n\n")
                f.write(vr_final.validation_table())
                f.write("\n\n")
                f.write(f"## Post\n\n{post_text}\n\n")
                if sources:
                    f.write(f"## Sources\n\n{sources}\n\n")
                if image_brief:
                    f.write(f"## Image Brief\n\n{image_brief}\n\n")

            val_icon = "✅ PASS" if vr_final.passed else "⚠️ WARN"
            summary_rows.append((slot["slot"], md_path.name, slot["datetime_str"], None, word_count, val_icon))
            print(f"SLOT_DONE:{slot['slot']}", flush=True)

    # Write calendar_summary.md (Step 4)
    summary_path = posts_dir / "calendar_summary.md"
    mode_label = f"Series ({posts_per_series} posts/topic)" if series_enabled else "Standard (1 post/topic)"
    pass_count = sum(1 for p in posts if p["validation"]["passed"])
    warn_count = len(posts) - pass_count

    with open(summary_path, "w", encoding="utf-8") as f:
        f.write(f"# Content Calendar — Generated {gen_date}\n\n")
        f.write(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  \n")
        f.write(f"**Period:** {cal_cfg.get('period', '1_week')} | **Posts:** {len(posts)} | **Mode:** {mode_label}\n\n")
        f.write("## Posts\n\n")
        f.write("| # | File | Schedule | Series Part | Words | Validation |\n")
        f.write("|---|------|----------|-------------|-------|------------|\n")
        for row in summary_rows:
            slot_n, fname, sched, series_part, wc, vicon = row
            f.write(f"| {slot_n} | {fname} | {sched} | {series_part or '—'} | {wc} | {vicon} |\n")
        f.write(f"\n## Validation Summary\n\n")
        f.write(f"- Research Validation: {'✅ All PASS' if all(True for _ in posts) else '⚠️ Some FAIL'}\n")
        f.write(f"- Content Validation: ✅ {pass_count} PASS, ⚠️ {warn_count} WARN\n\n")
        f.write("## Next Steps\n\n")
        f.write("1. Review each post file in this folder\n")
        f.write(f"2. Mark as reviewed:  `python main.py mark-reviewed outputs/{gen_date}/`\n")
        f.write(f"3. Approve and schedule: `python main.py approve-post outputs/{gen_date}/post_01_Mon_*.md --schedule \"YYYY-MM-DD 12:30\"`\n")
        f.write("4. View LinkedIn calendar: https://www.linkedin.com/content/scheduled/\n")

    print(f"SUMMARY_PATH:{summary_path}", flush=True)
    print("CALENDAR_JSON:" + json.dumps(posts, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
