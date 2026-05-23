"""
Content Calendar — batch post generation, scheduling, and review.
Reads config/calendar_config.yaml, generates N posts for the configured period,
then either queues them for review or schedules directly (autopilot).
"""
import json
import uuid
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import Optional

import yaml
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
from rich.prompt import Prompt, Confirm
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

console = Console()

PROJECT_ROOT    = Path(__file__).parent.parent.parent.parent
CALENDAR_CONFIG = PROJECT_ROOT / "config" / "calendar_config.yaml"
COSTAR_CONFIG   = PROJECT_ROOT / "config" / "costar_config.yaml"
DAY_NAMES       = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]

PERIOD_WEEKS = {
    "1_week":   1,
    "1_month":  4,
    "3_months": 13,
}

TOPIC_POOL = [
    "AI inference infrastructure on Kubernetes — cost and multi-tenancy",
    "OpenTelemetry production maturity and observability consolidation",
    "Platform engineering as a product — IDP adoption patterns",
    "FinOps for AI/ML workloads — controlling GPU compute spend",
    "Terraform vs OpenTofu — enterprise IaC fork decision",
    "Zero-trust security architecture in cloud-native environments",
    "Developer experience metrics — DORA + SPACE frameworks",
    "Cloud-native DR and business continuity patterns",
    "GitOps at scale — multi-cluster Flux / ArgoCD patterns",
    "eBPF in production — Kubernetes networking and security",
    "AI-assisted code review and platform engineering workflows",
    "Regulated industry cloud adoption — compliance-as-code",
    "Service mesh consolidation — Cilium ambient vs Istio sidecar",
    "WASM on the edge — next-gen serverless workloads",
    "Engineering org design for AI product delivery",
]


class ContentCalendar:
    """Generates and manages a full content calendar of LinkedIn posts."""

    def __init__(self):
        self.cfg           = self._load_config()
        self.cal           = self.cfg.get("calendar", {})
        self.img_cfg       = self.cfg.get("image", {})
        self.mode          = self.cal.get("mode", "review")
        self.period        = self.cal.get("period", "1_week")
        self.ppw           = self.cal.get("posts_per_week", 3)
        self.days          = self.cal.get("posting_days", [0, 2, 4])
        self.sched         = self.cal.get("schedule", {"time": "17:30", "timezone": "Asia/Kolkata"})
        self.preview_first = self.cal.get("preview_first", True)
        self.auto_post     = self.cal.get("auto_post_to_linkedin", False)

    # ── Entry Point ───────────────────────────────────────────────────────────

    def generate(
        self,
        topic_override: Optional[str] = None,
        full: bool = False,
        continue_from: Optional[int] = None,
    ):
        """Full calendar generation flow.

        Default behaviour (preview_first=True, full=False):
          - Generate only Week 1 Day 1 for style review.
          - After approval, caller can re-invoke with continue_from=2 to finish.

        full=True: skip preview, generate the entire calendar at once.
        continue_from=N: generate from slot N onwards (used after preview approval).
        """
        self._print_header()
        full_schedule = self._build_schedule()
        self._preview_schedule(full_schedule)

        # Determine which slots to generate in this run
        skip_preview = full or (continue_from is not None)
        if not skip_preview and self.preview_first:
            schedule = full_schedule[:1]
            console.print(
                "\n[bold yellow]Preview mode:[/bold yellow] generating [bold]Week 1 / Day 1[/bold] "
                "only so you can review the writing style before committing to the full calendar.\n"
                "[dim]Run with --full to generate all at once, "
                "or --continue-from 2 after approving the preview.[/dim]"
            )
        elif continue_from is not None:
            schedule = [s for s in full_schedule if s["slot"] >= continue_from]
            console.print(f"\n[dim]Continuing from slot {continue_from}...[/dim]")
        else:
            schedule = full_schedule

        if not Confirm.ask(
            f"\nGenerate [bold]{len(schedule)}[/bold] post(s) for this schedule?",
            default=True
        ):
            console.print("[yellow]Cancelled.[/yellow]")
            return

        posts = self._generate_all_posts(schedule, topic_override)
        calendar_path = self._save_calendar(posts)

        if self.mode == "review":
            self._review_loop(posts)
        else:
            self._autopilot_schedule(posts)

        remaining = len(full_schedule) - max(p["slot"] for p in posts)
        note = ""
        if remaining > 0 and not full and continue_from is None and self.preview_first:
            note = (
                f"\n\n  [dim]Style approved? Run:[/dim]\n"
                f"  [cyan]python main.py generate-calendar --continue-from 2[/cyan]\n"
                f"  [dim]to generate the remaining {remaining} post(s).[/dim]"
            )

        console.print(Panel(
            f"[bold green]Calendar run complete![/bold green]\n\n"
            f"  Posts generated : {len(posts)}\n"
            f"  Mode            : {self.mode.title()}\n"
            f"  Auto-post       : {'YES' if self.auto_post else 'NO (schedule only)'}\n"
            f"  Saved to        : {calendar_path}"
            + note,
            border_style="green",
        ))

    # ── Schedule Builder ──────────────────────────────────────────────────────

    def _build_schedule(self) -> list[dict]:
        """Return list of {slot, date, weekday, datetime_str} for the configured period."""
        weeks  = PERIOD_WEEKS.get(self.period, 4)
        today  = date.today()
        # Start from next Monday
        start  = today + timedelta(days=(7 - today.weekday()) % 7 or 7)
        time_h, time_m = map(int, self.sched["time"].split(":"))

        slots, slot_num = [], 1
        current = start
        end     = start + timedelta(weeks=weeks)

        while current < end:
            if current.weekday() in self.days:
                dt = datetime(current.year, current.month, current.day, time_h, time_m)
                slots.append({
                    "slot":         slot_num,
                    "date":         current.isoformat(),
                    "weekday":      DAY_NAMES[current.weekday()],
                    "datetime_str": dt.strftime("%Y-%m-%d %H:%M"),
                    "timezone":     self.sched.get("timezone", "UTC"),
                })
                slot_num += 1
            current += timedelta(days=1)

        return slots

    def _preview_schedule(self, schedule: list[dict]):
        table = Table(title=f"Content Schedule — {self.period.replace('_',' ').title()}", border_style="cyan")
        table.add_column("#",        style="bold cyan", justify="right")
        table.add_column("Date")
        table.add_column("Day")
        table.add_column("Time")
        table.add_column("Timezone", style="dim")
        for s in schedule[:10]:
            table.add_row(str(s["slot"]), s["date"], s["weekday"],
                          self.sched["time"], s["timezone"])
        if len(schedule) > 10:
            table.add_row("...", f"+ {len(schedule)-10} more", "", "", "")
        console.print(table)

    # ── Post Generation ───────────────────────────────────────────────────────

    def _generate_all_posts(self, schedule: list[dict], topic_override: Optional[str]) -> list[dict]:
        """Run research + content agent for every slot."""
        from linkedin_post_agent.utils.llm_factory import LLMFactory
        from linkedin_post_agent.tools.image_tool import ImageGenerationTool
        import sys, os
        sys.path.insert(0, str(PROJECT_ROOT / "src"))

        factory   = LLMFactory(str(PROJECT_ROOT / "config" / "llm_config.yaml"))
        img_tool  = ImageGenerationTool(self.img_cfg) if self.img_cfg.get("enabled") else None
        costar    = self._load_costar()

        posts = []
        total = len(schedule)

        console.print()
        console.print(Rule(f"[bold green]Generating {total} posts[/bold green]"))

        for i, slot in enumerate(schedule, 1):
            topic = topic_override or TOPIC_POOL[(i - 1) % len(TOPIC_POOL)]
            console.print(f"\n[bold cyan][{i}/{total}][/bold cyan] Topic: [italic]{topic}[/italic]")

            # Research
            console.print("  [dim]Research...[/dim]", end="")
            research = self._run_research(factory, topic)
            console.print(" [green]done[/green]")

            # Content
            console.print("  [dim]Writing post...[/dim]", end="")
            post_text, sources, image_brief = self._run_content(factory, research, costar)
            console.print(" [green]done[/green]")

            # Image
            image_path = None
            if img_tool:
                console.print("  [dim]Generating image...[/dim]", end="")
                image_path = img_tool.generate(post_text, topic)
                console.print(f" [green]{'done' if image_path else 'skipped'}[/green]")

            post_data = {
                "id":           uuid.uuid4().hex[:8],
                "slot":         slot["slot"],
                "date":         slot["date"],
                "weekday":      slot["weekday"],
                "schedule_time": slot["datetime_str"],
                "timezone":     slot["timezone"],
                "topic":        topic,
                "post":         post_text,
                "sources":      sources,
                "image_brief":  image_brief,
                "image_path":   image_path,
                "status":       "pending",  # pending | approved | rejected | published | scheduled
            }
            posts.append(post_data)
            self._save_post_file(post_data)

        return posts

    def _run_research(self, factory, topic: str) -> str:
        from linkedin_post_agent.tools.perplexity_tool import PerplexityResearchTool
        import os
        from linkedin_post_agent.utils.llm_factory import LLMFactory
        agents_cfg = self._load_yaml(PROJECT_ROOT / "config" / "agents.yaml")
        tasks_cfg  = self._load_yaml(PROJECT_ROOT / "config" / "tasks.yaml")
        from crewai import Agent, Task, Crew, Process
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

    def _run_content(self, factory, research: str, costar: str) -> tuple[str, str, str]:
        from run_headless import _parse_output, build_costar_prompt
        import yaml as _yaml
        from crewai import Agent, Task, Crew, Process
        agents_cfg = self._load_yaml(PROJECT_ROOT / "config" / "agents.yaml")
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
            expected_output="LinkedIn post + ## SOURCES + ## IMAGE_BRIEF",
            agent=agent,
        )
        crew = Crew(agents=[agent], tasks=[task], process=Process.sequential, verbose=False)
        raw = str(crew.kickoff()).strip()
        return _parse_output(raw)

    # ── Review Loop ───────────────────────────────────────────────────────────

    def _review_loop(self, posts: list[dict]):
        console.print()
        console.print(Rule("[bold yellow]Review Mode — Approve / Revise each post[/bold yellow]"))
        approved, skipped = 0, 0

        for i, post in enumerate(posts, 1):
            console.print(f"\n[bold cyan]Post {i}/{len(posts)}[/bold cyan]  "
                          f"[dim]{post['weekday']} {post['date']} at {self.sched['time']}[/dim]")
            console.print(f"[italic dim]Topic: {post['topic']}[/italic dim]\n")

            console.print(Panel(
                Text(post["post"]),
                title="[bold]LinkedIn Post Preview[/bold]",
                border_style="yellow",
                subtitle=f"[dim]{len(post['post'])} chars · ~{len(post['post'].split())} words[/dim]",
                padding=(1, 2),
            ))

            if post.get("sources"):
                console.print(f"\n[dim]Sources: {post['sources'][:200]}[/dim]")
            if post.get("image_path"):
                console.print(f"[green]Image:[/green] {post['image_path']}")

            action = Prompt.ask(
                "\n[bold]Action[/bold]",
                choices=["approve", "skip", "quit"],
                default="approve",
            )

            if action == "approve":
                post["status"] = "approved"
                self._schedule_single(post)
                approved += 1
            elif action == "skip":
                post["status"] = "skipped"
                skipped += 1
            elif action == "quit":
                console.print("[yellow]Review stopped.[/yellow]")
                break

        console.print(Panel(
            f"[bold]Review complete[/bold]\n\n"
            f"  Approved : [green]{approved}[/green]\n"
            f"  Skipped  : [yellow]{skipped}[/yellow]",
            border_style="cyan",
        ))

    # ── Autopilot ─────────────────────────────────────────────────────────────

    def _autopilot_schedule(self, posts: list[dict]):
        console.print()
        console.print(Rule("[bold blue]Autopilot — Scheduling all posts[/bold blue]"))
        for post in posts:
            post["status"] = "scheduled"
            self._schedule_single(post)
        console.print(f"[green]{len(posts)} posts scheduled.[/green]")
        console.print("[dim]Run `python main.py scheduler` to start the daemon.[/dim]")

    def _schedule_single(self, post: dict):
        try:
            from linkedin_post_agent.utils.scheduler import PostScheduler
            dt = datetime.strptime(post["schedule_time"], "%Y-%m-%d %H:%M")
            sched = PostScheduler()
            job_id = sched.schedule_post(post["post"], dt)
            post["job_id"] = job_id
            console.print(f"  [green]Scheduled[/green] post {post['id']} → {post['schedule_time']}")
        except Exception as e:
            console.print(f"  [red]Schedule failed for {post['id']}: {e}[/red]")

    # ── Persistence ───────────────────────────────────────────────────────────

    def _save_calendar(self, posts: list[dict]) -> str:
        out_dir = PROJECT_ROOT / "outputs"
        out_dir.mkdir(exist_ok=True)
        ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = out_dir / f"calendar_{self.period}_{ts}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(posts, f, indent=2, ensure_ascii=False)
        console.print(f"[dim]Calendar saved: {path}[/dim]")
        return str(path)

    def _save_post_file(self, post: dict):
        posts_dir = PROJECT_ROOT / "outputs" / "posts"
        posts_dir.mkdir(parents=True, exist_ok=True)
        path = posts_dir / f"post_{post['slot']:02d}_{post['date']}.md"
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"# Post {post['slot']} — {post['weekday']} {post['date']}\n\n")
            f.write(f"**Topic:** {post['topic']}\n")
            f.write(f"**Schedule:** {post['schedule_time']} {post['timezone']}\n\n")
            f.write(f"## Post\n\n{post['post']}\n\n")
            if post.get("sources"):
                f.write(f"## Sources\n\n{post['sources']}\n\n")
            if post.get("image_brief"):
                f.write(f"## Image Brief\n\n{post['image_brief']}\n\n")
            if post.get("image_path"):
                f.write(f"## Image\n\n`{post['image_path']}`\n")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _load_config(self) -> dict:
        if CALENDAR_CONFIG.exists():
            return self._load_yaml(CALENDAR_CONFIG)
        return {}

    def _load_costar(self) -> str:
        if COSTAR_CONFIG.exists():
            cfg = self._load_yaml(COSTAR_CONFIG)
            from run_headless import build_costar_prompt
            return build_costar_prompt(cfg)
        return ""

    def _load_yaml(self, path) -> dict:
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    def _print_header(self):
        cal = self.cal
        days = ", ".join(DAY_NAMES[d] for d in self.days)
        weeks = PERIOD_WEEKS.get(self.period, 4)
        total = weeks * self.ppw
        console.print(Panel(
            f"[bold cyan]Content Calendar Generator[/bold cyan]\n\n"
            f"  Period     : {self.period.replace('_',' ').title()}\n"
            f"  Frequency  : {self.ppw}x/week ({days})\n"
            f"  Posts      : ~{total} posts\n"
            f"  Time       : {self.sched['time']} {self.sched.get('timezone','')}\n"
            f"  Mode       : {self.mode.title()}\n"
            f"  Images     : {'Enabled (' + self.img_cfg.get('provider','') + ')' if self.img_cfg.get('enabled') else 'Disabled'}",
            border_style="cyan",
            padding=(1, 3),
        ))
