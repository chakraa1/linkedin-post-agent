"""
LinkedIn Post Crew — orchestrates the full 7-step pipeline (interactive mode).

Steps:
  1  Competitor analysis + trend research (research_agent)
  1b Research validation gate (research_validator)
  2  Content creation — COSTAR framework (content_writer)
  2b Auto-validation loop — 9 rules A-I (PostValidator)
  2c Human review loop
  2d Edit validation after each human revision (edit_validator)
  3  Explicit approval → LinkedIn native scheduler (PostScheduler)
"""
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml
from crewai import Agent, Crew, Process, Task
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table

from .tools.linkedin_tool import LinkedInTool
from .tools.perplexity_tool import PerplexityResearchTool
from .tools.review_tool import HumanReviewTool
from .utils.llm_factory import LLMFactory
from .utils.post_validator import PostValidator, _RULE_LABELS
from .utils.scheduler import PostScheduler

console = Console()

PROJECT_ROOT = Path(__file__).parent.parent.parent


class LinkedInPostCrew:
    """End-to-end LinkedIn post creation and publishing crew (interactive mode)."""

    MAX_REVISIONS = 10

    def __init__(
        self,
        llm_config_path: Optional[str] = None,
        dry_run: bool = False,
    ):
        if llm_config_path is None:
            llm_config_path = str(PROJECT_ROOT / "config" / "llm_config.yaml")

        self.dry_run = dry_run
        self.llm_factory = LLMFactory(llm_config_path)
        self.scheduler = PostScheduler()
        self.reviewer = HumanReviewTool()
        self.validator = PostValidator()
        self._agents_cfg = self._load_yaml(PROJECT_ROOT / "config" / "agents.yaml")
        self._tasks_cfg = self._load_yaml(PROJECT_ROOT / "config" / "tasks.yaml")

    # ── Entry Point ───────────────────────────────────────────────────────────

    def run(self, topic: Optional[str] = None, schedule_time: Optional[str] = None):
        """Execute the full pipeline."""
        self._print_header()

        # ── Phase 1: Competitor Analysis + Trend Research ─────────────────────
        console.print(Rule("[bold cyan]Phase 1 — Competitor Analysis & Research[/bold cyan]"))
        console.print("[dim]Analysing senior leadership profiles (SVP / Director / Head of Cloud / CTO)...[/dim]")
        research = self._phase_research(topic)
        console.print(Panel(research[:600] + "…", title="Research + Competitive Brief", border_style="dim"))

        # ── Phase 1b: Research Validation Gate ───────────────────────────────
        console.print()
        console.print(Rule("[bold cyan]Phase 1b — Research Validation[/bold cyan]"))
        rv_passed = self._phase_validate_research(research)
        if rv_passed:
            console.print("[green]Research validation: PASS[/green]")
        else:
            console.print("[yellow]Research validation: WARN — proceeding with incomplete research[/yellow]")

        # ── Phase 2: Content Creation + Auto-Validation + Human Review ────────
        console.print()
        console.print(Rule("[bold green]Phase 2 — Content Creation & Review[/bold green]"))
        approved_post, sources = self._phase_review_loop(research)

        if approved_post is None:
            console.print("\n[yellow]Workflow cancelled.[/yellow]")
            return

        # ── Phase 3: Publish or Schedule ─────────────────────────────────────
        console.print()
        console.print(Rule("[bold blue]Phase 3 — Publish / Schedule[/bold blue]"))
        self._phase_publish(approved_post, schedule_time)

    # ── Phase 1: Research + Competitive Analysis ──────────────────────────────

    def _phase_research(self, topic: Optional[str]) -> str:
        """
        Run the research agent — covers both trend research (Part A) and
        competitive landscape analysis of 9 senior leadership role profiles (Part B).
        """
        agent = self._make_research_agent()
        cfg = self._tasks_cfg["research_task"]
        extra = f"\n\nFocus specifically on: {topic}" if topic else ""

        task = Task(
            description=cfg["description"] + extra,
            expected_output=cfg["expected_output"],
            agent=agent,
        )
        crew = Crew(agents=[agent], tasks=[task], process=Process.sequential, verbose=True)
        return str(crew.kickoff()).strip()

    # ── Phase 1b: Research Validation ────────────────────────────────────────

    def _phase_validate_research(self, research: str) -> bool:
        """
        Gate: validates research has 4 required sections, 3+ real citations,
        a concrete data point, and a differentiation angle.
        Returns True = PASS, False = FAIL.
        """
        agent = self._make_research_validator_agent()
        cfg = self._tasks_cfg["validate_research_task"]
        desc = cfg["description"].replace("{research_output}", research)

        task = Task(description=desc, expected_output=cfg["expected_output"], agent=agent)
        crew = Crew(agents=[agent], tasks=[task], process=Process.sequential, verbose=False)
        result = str(crew.kickoff()).strip()

        if "VERDICT: FAIL" in result or "VERDICT:FAIL" in result:
            console.print(Panel(result, title="[yellow]Research Validation Issues[/yellow]", border_style="yellow"))
            return False
        return True

    # ── Phase 2: Review Loop ──────────────────────────────────────────────────

    def _phase_review_loop(self, research: str) -> tuple[Optional[str], str]:
        """
        Generate post → auto-validate (9 rules) → human review → edit → repeat.
        Returns (approved_post, sources) or (None, "").
        """
        feedback: Optional[str] = None
        post_content, sources = "", ""

        for iteration in range(1, self.MAX_REVISIONS + 1):

            # Phase 2a: Generate post
            post_content, sources = self._generate_post(research, feedback, iteration)

            # Phase 2b: Auto-validate (9 rules A-I) + auto-revise up to MAX_RETRIES
            console.print(f"\n[dim]Running 9-rule validation (attempt {iteration})...[/dim]")
            for attempt in range(self.validator.MAX_RETRIES):
                vr = self.validator.validate(post_content, sources)
                if vr.passed:
                    break
                console.print(f"[yellow]Auto-revision {attempt + 1}: {'; '.join(vr.issues[:2])}[/yellow]")
                post_content, sources = self._generate_post(
                    research, vr.as_revision_feedback(), iteration
                )
            vr_final = self.validator.validate(post_content, sources)
            self._print_validation_report(vr_final)

            # Phase 2c: Human review
            action, feedback = self.reviewer.review(post_content, iteration)

            if action == "approve":
                self._save_draft(post_content, sources, vr_final, "approved")
                return post_content, sources

            if action == "quit":
                return None, ""

            # Phase 2d: Edit — apply human feedback, then run edit_validator
            console.print(f"[dim]Revising with feedback… (iteration {iteration + 1})[/dim]\n")
            post_content, sources = self._generate_post(research, feedback, iteration + 1)
            self._validate_edit(
                original_post=post_content,
                feedback_applied=feedback,
                revised_post=post_content,
            )

        console.print(f"[red]Reached maximum {self.MAX_REVISIONS} revisions.[/red]")
        return None, ""

    # ── Phase 2b helper: 9-rule validation display ────────────────────────────

    def _print_validation_report(self, vr):
        table = Table(title="Validation Report (9 Rules A-I)", border_style="green" if vr.passed else "yellow")
        table.add_column("Rule", style="bold")
        table.add_column("Check")
        table.add_column("Status")

        for letter, label in _RULE_LABELS.items():
            status = vr.rule_status.get(letter, "✅ PASS")
            color = "green" if "PASS" in status else ("yellow" if "WARN" in status else "red")
            table.add_row(letter, label, f"[{color}]{status}[/{color}]")

        console.print(table)
        if vr.issues:
            console.print("[yellow]Issues to review:[/yellow]")
            for issue in vr.issues:
                console.print(f"  [yellow]·[/yellow] {issue}")

    # ── Phase 2d helper: Edit validation ────────────────────────────────────

    def _validate_edit(self, original_post: str, feedback_applied: str, revised_post: str) -> bool:
        """Run edit_validator agent to check revision didn't introduce regressions."""
        try:
            agent = self._make_edit_validator_agent()
            cfg = self._tasks_cfg["validate_edit_task"]
            desc = (
                cfg["description"]
                .replace("{original_post}", original_post[:800])
                .replace("{feedback_applied}", feedback_applied[:400])
                .replace("{revised_post}", revised_post[:800])
            )
            task = Task(description=desc, expected_output=cfg["expected_output"], agent=agent)
            crew = Crew(agents=[agent], tasks=[task], process=Process.sequential, verbose=False)
            result = str(crew.kickoff()).strip()

            if "VERDICT: FAIL" in result or "VERDICT:FAIL" in result:
                console.print(Panel(result, title="[yellow]Edit Validation Issues[/yellow]", border_style="yellow"))
                return False
            console.print("[green]Edit validation: PASS[/green]")
            return True
        except Exception as e:
            console.print(f"[dim]Edit validator skipped: {e}[/dim]")
            return True

    # ── Post generation ───────────────────────────────────────────────────────

    def _generate_post(
        self, research: str, feedback: Optional[str], iteration: int
    ) -> tuple[str, str]:
        """
        Run the content writer agent. Returns (post_body, sources_block).
        On first iteration writes a fresh post; subsequent iterations apply feedback.
        """
        agent = self._make_content_agent()

        if feedback and iteration > 1:
            cfg = self._tasks_cfg["edit_task"]
            description = cfg["description"].format(
                original_post="(previous draft)", feedback=feedback
            )
            expected_output = cfg["expected_output"]
        else:
            cfg = self._tasks_cfg["content_task"]
            description = cfg["description"].format(research=research)
            expected_output = cfg["expected_output"]

        task = Task(description=description, expected_output=expected_output, agent=agent)
        crew = Crew(agents=[agent], tasks=[task], process=Process.sequential, verbose=False)
        raw = str(crew.kickoff()).strip()

        # Split off ## SOURCES block if present
        sources = ""
        if "## SOURCES" in raw:
            parts = raw.split("## SOURCES", 1)
            raw = parts[0].strip()
            sources = parts[1].strip().split("## ")[0].strip()

        return raw, sources

    # ── Phase 3: Publish / Schedule ──────────────────────────────────────────

    def _phase_publish(self, post_content: str, schedule_time: Optional[str] = None):
        if self.dry_run:
            console.print(Panel(
                f"[bold yellow]DRY RUN — post not published[/bold yellow]\n\n{post_content}",
                border_style="yellow",
            ))
            return

        if schedule_time is None:
            post_now, schedule_time = self.reviewer.confirm_schedule()
        else:
            post_now = False

        if post_now or schedule_time is None:
            self._post_now(post_content)
        else:
            self._schedule_post(post_content, schedule_time)

    def _post_now(self, post_content: str):
        self.scheduler.post_now(post_content)

    def _schedule_post(self, post_content: str, schedule_time_str: str):
        try:
            scheduled_dt = datetime.strptime(schedule_time_str, "%Y-%m-%d %H:%M")
        except ValueError:
            console.print(f"[red]Invalid date format '{schedule_time_str}'. Use: YYYY-MM-DD HH:MM[/red]")
            return
        self.scheduler.schedule_post(post_content, scheduled_dt)

    # ── Agent Factories ───────────────────────────────────────────────────────

    def _make_research_agent(self) -> Agent:
        cfg = self._agents_cfg["research_agent"]
        tools = [PerplexityResearchTool()] if os.getenv("PERPLEXITY_API_KEY") else []
        return Agent(
            role=cfg["role"],
            goal=cfg["goal"],
            backstory=cfg["backstory"],
            tools=tools,
            llm=self.llm_factory.get_llm("research_agent"),
            verbose=True,
            allow_delegation=False,
            max_iter=3,
        )

    def _make_research_validator_agent(self) -> Agent:
        cfg = self._agents_cfg["research_validator"]
        return Agent(
            role=cfg["role"],
            goal=cfg["goal"],
            backstory=cfg["backstory"],
            llm=self.llm_factory.get_llm("research_agent"),
            verbose=False,
            allow_delegation=False,
            max_iter=1,
        )

    def _make_content_agent(self) -> Agent:
        cfg = self._agents_cfg["content_writer"]
        return Agent(
            role=cfg["role"],
            goal=cfg["goal"],
            backstory=cfg["backstory"],
            llm=self.llm_factory.get_llm("content_writer"),
            verbose=True,
            allow_delegation=False,
            max_iter=3,
        )

    def _make_edit_validator_agent(self) -> Agent:
        cfg = self._agents_cfg["edit_validator"]
        return Agent(
            role=cfg["role"],
            goal=cfg["goal"],
            backstory=cfg["backstory"],
            llm=self.llm_factory.get_llm("content_writer"),
            verbose=False,
            allow_delegation=False,
            max_iter=1,
        )

    # ── Draft Save (date-based folder) ───────────────────────────────────────

    def _save_draft(self, content: str, sources: str, vr, status: str):
        """Save post to outputs/YYYY-MM-DD/ with validation report — same format as calendar output."""
        gen_date = datetime.now().strftime("%Y-%m-%d")
        output_dir = PROJECT_ROOT / "outputs" / gen_date
        output_dir.mkdir(parents=True, exist_ok=True)

        ts = datetime.now().strftime("%H%M%S")
        path = output_dir / f"post_{status}_{ts}.md"

        word_count = len(content.split())
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"# Post — {status.upper()} {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n")
            f.write(f"**Words:** {word_count}\n")
            f.write(f"**Validation:** {'✅ PASS' if vr.passed else '⚠️ WARN — ' + '; '.join(vr.issues[:1])}\n\n")
            f.write("## Validation Report\n\n")
            f.write(vr.validation_table())
            f.write("\n\n")
            f.write(f"## Post\n\n{content}\n\n")
            if sources:
                f.write(f"## Sources\n\n{sources}\n\n")

        console.print(f"[dim]Saved: {path}[/dim]")

    # ── Utilities ─────────────────────────────────────────────────────────────

    def _load_yaml(self, path: Path) -> dict:
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    def _print_header(self):
        console.print(Panel(
            "[bold cyan]LinkedIn Post Agent — Interactive Mode[/bold cyan]\n"
            "[dim]Competitor Analysis → Research → Content → 9-Rule Validation → Human Review → LinkedIn[/dim]\n\n"
            f"Dry run: {'[yellow]YES[/yellow]' if self.dry_run else '[green]NO[/green]'}",
            border_style="cyan",
            padding=(1, 3),
        ))
