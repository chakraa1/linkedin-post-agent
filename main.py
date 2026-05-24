#!/usr/bin/env python3
"""
LinkedIn Post Agent — CLI entry point.

Commands:
  setup              First-time configuration wizard (calendar, schedule, images)
  run                Single post: research → write → review → publish/schedule
  generate-calendar  Batch-generate full content calendar (1 week / 1 month / 3 months)
  auth               Authenticate with LinkedIn OAuth 2.0
  list-drafts        List all locally generated draft posts
  approve-post       Approve a draft and post/schedule it to LinkedIn (explicit gate)
  list-posts         Show all scheduled/published posts tracked locally
  cancel-post        Cancel a pending scheduled post
  config             Show LLM configuration
  config-show        Show calendar / schedule configuration
"""
import os
import sys
from datetime import datetime
from pathlib import Path

import click
from dotenv import load_dotenv
from rich.console import Console

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent / "src"))

from linkedin_post_agent.crew import LinkedInPostCrew
from linkedin_post_agent.tools.linkedin_tool import LinkedInTool
from linkedin_post_agent.utils.llm_factory import LLMFactory
from linkedin_post_agent.utils.scheduler import PostScheduler
from linkedin_post_agent.utils.setup_wizard import SetupWizard
from linkedin_post_agent.utils.calendar import ContentCalendar

console = Console()


@click.group()
@click.version_option(version="0.1.0", prog_name="linkedin-post-agent")
def cli():
    """LinkedIn Post Agent — AI-powered content creation & scheduling."""
    pass


# ── Main pipeline ─────────────────────────────────────────────────────────────

@cli.command()
@click.option(
    "--topic", "-t",
    default=None,
    help="Specific topic to research (optional; auto-selected if omitted).",
)
@click.option(
    "--schedule", "-s",
    default=None,
    metavar="YYYY-MM-DD HH:MM",
    help="Pre-supply a schedule time to skip the interactive prompt.",
)
@click.option(
    "--llm-config", "-l",
    default="config/llm_config.yaml",
    show_default=True,
    help="Path to LLM config YAML.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Run full pipeline but skip posting to LinkedIn.",
)
def run(topic, schedule, llm_config, dry_run):
    """Research a topic, create a LinkedIn post, review, then publish or schedule."""
    crew = LinkedInPostCrew(llm_config_path=llm_config, dry_run=dry_run)
    crew.run(topic=topic, schedule_time=schedule)


# ── LinkedIn Auth ─────────────────────────────────────────────────────────────

@cli.command()
def auth():
    """Authenticate with LinkedIn via OAuth 2.0 (one-time setup)."""
    console.print(
        "\n[bold]LinkedIn OAuth Setup[/bold]\n"
        "Prerequisites:\n"
        "  1. Create a LinkedIn Developer App at https://developer.linkedin.com\n"
        "  2. Add products: 'Share on LinkedIn' + 'Sign In with LinkedIn'\n"
        "  3. Set redirect URI: [cyan]http://localhost:8080/callback[/cyan]\n"
        "  4. Add LINKEDIN_CLIENT_ID and LINKEDIN_CLIENT_SECRET to your .env\n"
    )
    tool = LinkedInTool()
    tool.authenticate()


# ── Draft management & selective approval ─────────────────────────────────────

@cli.command("list-drafts")
@click.option("--date", "-d", default=None,
              help="Filter to a specific generation date folder (e.g. 2026-05-23).")
def list_drafts(date):
    """List all locally generated draft posts by date folder."""
    from rich.table import Table

    outputs_dir = Path("outputs")
    if not outputs_dir.exists():
        console.print("[dim]No outputs directory found.[/dim]")
        return

    # Collect date folders
    date_dirs = sorted(
        [d for d in outputs_dir.iterdir() if d.is_dir() and d.name[0].isdigit()],
        reverse=True,
    )
    if date:
        date_dirs = [d for d in date_dirs if d.name == date]

    if not date_dirs:
        console.print("[dim]No draft folders found under outputs/[/dim]")
        return

    for folder in date_dirs:
        md_files = sorted(folder.glob("post_*.md"))
        if not md_files:
            continue
        table = Table(title=f"Drafts — {folder.name}", border_style="cyan")
        table.add_column("File", style="bold cyan")
        table.add_column("Words")
        table.add_column("Validation")
        table.add_column("Modified")

        for f in md_files:
            raw = f.read_text(encoding="utf-8")
            words = "—"
            val   = "—"
            for line in raw.splitlines():
                if line.startswith("**Words:**"):
                    words = line.split(":", 1)[1].strip()
                if line.startswith("**Validation:**"):
                    val = line.split(":", 1)[1].strip()
            stat = f.stat()
            table.add_row(
                f.name, words, val,
                datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
            )
        console.print(table)

        summary = folder / "calendar_summary.md"
        if summary.exists():
            console.print(f"[dim]  Summary: {summary}[/dim]")

    console.print(
        f"\n[dim]To mark a folder as reviewed: "
        f"python main.py mark-reviewed outputs/YYYY-MM-DD/[/dim]"
    )


@cli.command("mark-reviewed")
@click.argument("folder", metavar="FOLDER")
def mark_reviewed(folder):
    """
    Mark all posts in a generated date folder as reviewed (Step 5).

    After reviewing the .md files manually, run this command to register
    them in the database as 'reviewed' — making them eligible for approval
    and LinkedIn posting. Duplicate content is automatically rejected.

    Example:
      python main.py mark-reviewed outputs/2026-05-23/
    """
    import re as _re

    folder_path = Path(folder)
    if not folder_path.exists() or not folder_path.is_dir():
        console.print(f"[red]Folder not found: {folder}[/red]")
        raise SystemExit(1)

    md_files = sorted(folder_path.glob("post_*.md"))
    if not md_files:
        console.print(f"[yellow]No post_*.md files found in {folder}[/yellow]")
        return

    ps = PostScheduler()
    registered, skipped = 0, 0

    for f in md_files:
        raw = f.read_text(encoding="utf-8")

        # Extract post body
        post_content = raw
        if "## Post\n\n" in raw:
            post_content = raw.split("## Post\n\n", 1)[1]
            if "\n\n## " in post_content:
                post_content = post_content.split("\n\n## ", 1)[0]
        post_content = post_content.strip()

        # Extract metadata from front-matter lines
        topic, series_part, word_count = "", "", 0
        for line in raw.splitlines():
            if line.startswith("**Topic:**"):
                topic = line.split(":", 1)[1].strip()
            elif line.startswith("**Series:**"):
                series_part = line.split(":", 1)[1].strip()
            elif line.startswith("**Words:**"):
                try:
                    word_count = int(line.split(":", 1)[1].strip())
                except ValueError:
                    pass

        post_id, is_dup = ps.register_draft(
            content=post_content,
            review_folder=str(folder_path),
            topic=topic,
            series_part=series_part,
            word_count=word_count,
        )

        if is_dup:
            console.print(f"[yellow]  SKIP (duplicate): {f.name}[/yellow]")
            skipped += 1
        else:
            console.print(f"[green]  Registered [{post_id}]: {f.name}[/green]")
            registered += 1

    console.print(
        f"\n[bold]Done.[/bold] {registered} post(s) registered as reviewed, "
        f"{skipped} duplicate(s) skipped.\n"
        f"[dim]Next: python main.py approve-post <file> --schedule \"YYYY-MM-DD 12:30\"[/dim]"
    )


@cli.command("approve-post")
@click.argument("post_file", metavar="POST_FILE")
@click.option("--schedule", "-s", default=None, metavar="YYYY-MM-DD HH:MM",
              help="Schedule time (12:30 or 18:30 IST recommended).")
@click.option("--now", is_flag=True, default=False,
              help="Post immediately instead of scheduling.")
@click.option("--yes", "-y", is_flag=True, default=False,
              help="Skip confirmation prompt.")
def approve_post(post_file, schedule, now, yes):
    """
    Approve a reviewed draft and post or schedule it to LinkedIn (Step 5+6).

    EXPLICIT APPROVAL GATE — no content reaches LinkedIn without this command.
    The post must have been registered via mark-reviewed first.
    Duplicate content (already scheduled/published) is automatically blocked (Step 7).

    Recommended times: --schedule "YYYY-MM-DD 12:30"  (lunch peak)
                       --schedule "YYYY-MM-DD 18:30"  (evening peak)

    Examples:
      python main.py approve-post outputs/2026-05-26/post_01_Mon_2026-05-26.md --schedule "2026-05-26 12:30"
      python main.py approve-post outputs/2026-05-26/post_01_Mon_2026-05-26.md --now
    """
    from rich.text import Text
    from rich.panel import Panel

    path = Path(post_file)
    if not path.exists():
        console.print(f"[red]File not found: {post_file}[/red]")
        raise SystemExit(1)

    raw = path.read_text(encoding="utf-8")

    post_content = raw
    if "## Post\n\n" in raw:
        post_content = raw.split("## Post\n\n", 1)[1]
        if "\n\n## " in post_content:
            post_content = post_content.split("\n\n## ", 1)[0]
    post_content = post_content.strip()

    console.print()
    console.print(Panel(
        Text(post_content),
        title=f"[bold yellow]{path.name}[/bold yellow]",
        subtitle=f"[dim]{len(post_content.split())} words[/dim]",
        border_style="yellow",
        padding=(1, 3),
    ))
    console.print()

    if not now and not schedule:
        console.print("[red]Specify --now or --schedule YYYY-MM-DD HH:MM (e.g. 12:30 or 18:30)[/red]")
        raise SystemExit(1)

    confirmed = yes or click.confirm("[bold]Confirm: approve and post this to LinkedIn?[/bold]", default=False)
    if not confirmed:
        console.print("[yellow]Cancelled.[/yellow]")
        return

    ps = PostScheduler()
    try:
        if now:
            ps.post_now(post_content)
        else:
            try:
                dt = datetime.strptime(schedule, "%Y-%m-%d %H:%M")
            except ValueError:
                console.print("[red]Invalid date format. Use: YYYY-MM-DD HH:MM[/red]")
                raise SystemExit(1)
            ps.schedule_post(post_content, dt)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")


# ── Post tracking ─────────────────────────────────────────────────────────────

@cli.command("list-posts")
def list_posts():
    """List all posts tracked locally (scheduled, published, failed)."""
    ps = PostScheduler()
    ps.list_scheduled()


@cli.command("cancel-post")
@click.argument("post_id")
def cancel_post(post_id):
    """Mark a post cancelled in local DB. Also cancel it on linkedin.com/content/scheduled/."""
    ps = PostScheduler()
    ok = ps.cancel_post(post_id)
    if not ok:
        console.print(f"[red]Post '{post_id}' not found or cannot be cancelled.[/red]")


# ── Setup Wizard ──────────────────────────────────────────────────────────────

@cli.command()
@click.option("--reconfigure", is_flag=True, default=False,
              help="Re-run setup even if config already exists.")
def setup(reconfigure):
    """First-time setup: configure calendar period, schedule, mode, and image generation."""
    wizard = SetupWizard()
    wizard.run(force=reconfigure)


# ── Content Calendar ──────────────────────────────────────────────────────────

@cli.command("generate-calendar")
@click.option("--topic", "-t", default=None,
              help="Pin all posts to this topic (optional; auto-rotated if omitted).")
@click.option("--full", is_flag=True, default=False,
              help="Generate the full calendar at once, skipping the Week-1-Day-1 style preview.")
@click.option("--continue-from", default=None, type=int,
              help="Continue calendar generation from this slot number (after a preview run).")
def generate_calendar(topic, full, continue_from):
    """Batch-generate a full content calendar.

    Default: generates Week 1 Day 1 only for style review before committing
    to the full calendar. Use --full to skip the preview step.
    Use --continue-from N to generate remaining slots after approving the preview.
    """
    calendar = ContentCalendar()
    calendar.generate(topic_override=topic, full=full, continue_from=continue_from)


# ── Config show ───────────────────────────────────────────────────────────────

@cli.command("config-show")
def config_show():
    """Show current calendar and schedule configuration."""
    wizard = SetupWizard()
    wizard.show_config()


# ── LLM Config validation ─────────────────────────────────────────────────────

@cli.command()
@click.option("--llm-config", "-l", default="config/llm_config.yaml",
              show_default=True, help="Path to LLM config YAML.")
def config(llm_config):
    """Show resolved LLM configuration and API key status."""
    factory = LLMFactory(llm_config)
    factory.validate()


if __name__ == "__main__":
    cli()
