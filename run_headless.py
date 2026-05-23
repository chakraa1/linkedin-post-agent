"""
Headless runner — research + COSTAR-based content generation.
Outputs structured JSON: post, sources, image_brief, research.
"""
import json, os, sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent / "src"))

import yaml
from crewai import Agent, Crew, Process, Task

PROJECT_ROOT = Path(__file__).parent
COSTAR_CONFIG = PROJECT_ROOT / "config" / "costar_config.yaml"


def load_yaml(path):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_costar_prompt(cfg: dict) -> str:
    c = cfg["costar"]
    ctx   = c["context"]
    obj   = c["objective"]
    sty   = c["style"]
    tone  = c["tone"]
    aud   = c["audience"]
    fmt   = c["format"]

    secondary     = "\n".join(f"  - {s}" for s in obj.get("secondary", []))
    audience      = ", ".join(aud.get("primary", []))
    hashtags      = fmt.get("hashtags", "3-4 targeted hashtags")
    misc          = "\n".join(f"  - {m}" for m in tone.get("signals", []))
    avoid_list    = "\n".join(f"  - {a}" for a in sty.get("avoid", []))
    voice_guidance = sty.get("voice_guidance", sty.get("voice", "Professional but conversational"))

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
- Hashtags: {hashtags}
- No emoji. No bullet lists.

CRITICAL WRITING RULES (non-negotiable):
1. NEVER use an em dash (—). Replace with a comma, colon, or new sentence.
2. NEVER use corporate buzzwords: leverage, synergies, utilize, robust, seamless, cutting-edge, transformative, empower.
3. ALWAYS tie the post's insight to a concrete business outcome or ROI. Numbers beat adjectives.
4. INCLUDE at least one specific, real example — anonymize the organisation if needed (e.g. "a Tier 1 investment bank", "a major trading platform"). Made-up vague examples are not acceptable. The example must have a concrete outcome (metric, timeline, or decision).
5. Draw from the research provided. Use real data points and recent trends.
6. Adopt the persona of someone who has actually operated these systems at a global bank. Reference anonymised banking/regulated-industry context naturally.
7. Challenge a common misconception or vendor promise head-on.
8. Surface a non-obvious insight that only a practitioner with scale experience would have.
9. Use the competitive differentiation angle from the research (## Competitive Landscape section) if present.

After the post, output TWO additional sections using these exact headers:

## SOURCES
List 2-3 validated, authoritative references (real domains + article/report title).
Format: [Title] - domain.com
(No em dashes in sources either.)

## IMAGE_BRIEF
Describe a diagram or visual (100 words max) that would make this post shareable.
Include: type of visual (flowchart, comparison table, timeline, etc.), key elements,
and the single insight it should communicate at a glance.
A Mermaid diagram definition is a bonus if appropriate.
"""


def run(topic=None, feedback=None, previous_post=None):
    agents_cfg  = load_yaml(PROJECT_ROOT / "config" / "agents.yaml")
    tasks_cfg   = load_yaml(PROJECT_ROOT / "config" / "tasks.yaml")
    costar_cfg  = load_yaml(COSTAR_CONFIG) if COSTAR_CONFIG.exists() else {}

    from linkedin_post_agent.utils.llm_factory import LLMFactory
    from linkedin_post_agent.tools.perplexity_tool import PerplexityResearchTool
    factory = LLMFactory(str(PROJECT_ROOT / "config" / "llm_config.yaml"))

    # ── Phase 1: Research ─────────────────────────────────────────────────────
    research_result = os.getenv("_CACHED_RESEARCH", "")

    if not research_result:
        print("PHASE:research", flush=True)
        tools = [PerplexityResearchTool()] if os.getenv("PERPLEXITY_API_KEY") else []
        research_agent = Agent(
            role=agents_cfg["research_agent"]["role"],
            goal=agents_cfg["research_agent"]["goal"],
            backstory=agents_cfg["research_agent"]["backstory"],
            tools=tools,
            llm=factory.get_llm("research_agent"),
            verbose=False,
            allow_delegation=False,
            max_iter=3,
        )
        extra = f"\n\nFocus specifically on: {topic}" if topic else ""
        task = Task(
            description=tasks_cfg["research_task"]["description"] + extra,
            expected_output=tasks_cfg["research_task"]["expected_output"],
            agent=research_agent,
        )
        crew = Crew(agents=[research_agent], tasks=[task], process=Process.sequential, verbose=False)
        research_result = str(crew.kickoff()).strip()

    # ── Phase 1b: Research Validation ────────────────────────────────────────
    print("PHASE:validate_research", flush=True)
    rv_agent = Agent(
        role=agents_cfg["research_validator"]["role"],
        goal=agents_cfg["research_validator"]["goal"],
        backstory=agents_cfg["research_validator"]["backstory"],
        llm=factory.get_llm("research_agent"),
        verbose=False,
        allow_delegation=False,
        max_iter=1,
    )
    rv_desc = tasks_cfg["validate_research_task"]["description"].replace(
        "{research_output}", research_result
    )
    rv_task = Task(
        description=rv_desc,
        expected_output=tasks_cfg["validate_research_task"]["expected_output"],
        agent=rv_agent,
    )
    rv_crew = Crew(agents=[rv_agent], tasks=[rv_task], process=Process.sequential, verbose=False)
    rv_result = str(rv_crew.kickoff()).strip()
    if "VERDICT: FAIL" in rv_result or "VERDICT:FAIL" in rv_result:
        print(f"RESEARCH_VALIDATION_FAIL:{rv_result[:300]}", flush=True)
    else:
        print("RESEARCH_VALIDATION_PASS", flush=True)

    # ── Phase 2: COSTAR Content Generation ───────────────────────────────────
    print("PHASE:content", flush=True)
    content_agent = Agent(
        role=agents_cfg["content_writer"]["role"],
        goal=agents_cfg["content_writer"]["goal"],
        backstory=agents_cfg["content_writer"]["backstory"],
        llm=factory.get_llm("content_writer"),
        verbose=False,
        allow_delegation=False,
        max_iter=3,
    )

    costar_instructions = build_costar_prompt(costar_cfg) if costar_cfg else ""

    if feedback and previous_post:
        description = f"""REVISION REQUEST
Previous post:
{previous_post}

Feedback to incorporate:
{feedback}

{costar_instructions}

Research to draw from:
{research_result}

Apply the feedback precisely. Keep everything that worked. Maintain the COSTAR persona throughout.
Output the revised post followed by ## SOURCES and ## IMAGE_BRIEF sections."""
    else:
        description = f"""{costar_instructions}

Research to draw from:
{research_result}

Write the LinkedIn post now, then append ## SOURCES and ## IMAGE_BRIEF."""

    task = Task(
        description=description,
        expected_output=(
            "A LinkedIn post (150-200 words, COSTAR format), followed by "
            "## SOURCES (2-3 validated references) and ## IMAGE_BRIEF (diagram description + optional Mermaid)."
        ),
        agent=content_agent,
    )
    crew = Crew(agents=[content_agent], tasks=[task], process=Process.sequential, verbose=False)
    raw_output = str(crew.kickoff()).strip()

    # ── Validation + auto-revision loop ──────────────────────────────────────
    from linkedin_post_agent.utils.post_validator import PostValidator
    validator = PostValidator()
    post, sources, image_brief = _parse_output(raw_output)

    # Phase 2b: Edit validation — only when human feedback was applied
    if feedback and previous_post:
        print("PHASE:validate_edit", flush=True)
        ev_agent = Agent(
            role=agents_cfg["edit_validator"]["role"],
            goal=agents_cfg["edit_validator"]["goal"],
            backstory=agents_cfg["edit_validator"]["backstory"],
            llm=factory.get_llm("content_writer"),
            verbose=False,
            allow_delegation=False,
            max_iter=1,
        )
        ev_desc = (
            tasks_cfg["validate_edit_task"]["description"]
            .replace("{original_post}", previous_post)
            .replace("{feedback_applied}", feedback)
            .replace("{revised_post}", post)
        )
        ev_task = Task(
            description=ev_desc,
            expected_output=tasks_cfg["validate_edit_task"]["expected_output"],
            agent=ev_agent,
        )
        ev_crew = Crew(
            agents=[ev_agent], tasks=[ev_task], process=Process.sequential, verbose=False
        )
        ev_result = str(ev_crew.kickoff()).strip()
        if "VERDICT: FAIL" in ev_result or "VERDICT:FAIL" in ev_result:
            print(f"EDIT_VALIDATION_FAIL:{ev_result[:300]}", flush=True)
        else:
            print("EDIT_VALIDATION_PASS", flush=True)

    # Phase 2c: Content quality validation (PostValidator — 9 rules A-I)

    for attempt in range(validator.MAX_RETRIES):
        vr = validator.validate(post, sources)
        if vr.passed:
            break
        print(
            f"VALIDATION_FAIL:{attempt + 1}:{'; '.join(vr.issues[:2])}",
            flush=True,
        )
        revision_desc = (
            f"REVISION — attempt {attempt + 1} of {validator.MAX_RETRIES}\n\n"
            f"{vr.as_revision_feedback()}\n\n"
            f"Previous post:\n{post}\n\n"
            f"{costar_instructions}\n\n"
            f"Research:\n{research_result}\n\n"
            "Output the revised post followed by ## SOURCES and ## IMAGE_BRIEF."
        )
        rev_task = Task(
            description=revision_desc,
            expected_output=(
                "Revised LinkedIn post + ## SOURCES + ## IMAGE_BRIEF "
                "with every validation issue fixed."
            ),
            agent=content_agent,
        )
        rev_crew = Crew(
            agents=[content_agent],
            tasks=[rev_task],
            process=Process.sequential,
            verbose=False,
        )
        raw_output = str(rev_crew.kickoff()).strip()
        post, sources, image_brief = _parse_output(raw_output)

    vr_final = validator.validate(post, sources)
    if not vr_final.passed:
        print(f"VALIDATION_WARN:{'; '.join(vr_final.issues)}", flush=True)

    result = {
        "research": research_result,
        "post": post,
        "sources": sources,
        "image_brief": image_brief,
        "raw": raw_output,
        "validation": {"passed": vr_final.passed, "issues": vr_final.issues},
    }
    print("RESULT_JSON:" + json.dumps(result, ensure_ascii=False), flush=True)


def _parse_output(raw: str):
    """Split agent output into post, sources, image_brief."""
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


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--topic", default=None)
    parser.add_argument("--feedback", default=None)
    parser.add_argument("--previous-post", default=None)
    args = parser.parse_args()
    run(topic=args.topic, feedback=args.feedback, previous_post=args.previous_post)
