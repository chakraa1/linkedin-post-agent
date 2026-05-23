"""
Setup Wizard — one-time interactive configuration for calendar, schedule, and image settings.
Run via: python main.py setup
Re-run with: python main.py setup --reconfigure
Saves to: config/calendar_config.yaml
"""
from pathlib import Path
from typing import Optional
import yaml
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, Confirm, IntPrompt
from rich.table import Table
from rich.rule import Rule

console = Console()

PROJECT_ROOT  = Path(__file__).parent.parent.parent.parent
CALENDAR_CONFIG = PROJECT_ROOT / "config" / "calendar_config.yaml"

TIMEZONES = {
    "1": ("Asia/Kolkata",      "IST  — India Standard Time        UTC+5:30"),
    "2": ("US/Eastern",        "EST  — US Eastern Time            UTC-5"),
    "3": ("US/Pacific",        "PST  — US Pacific Time            UTC-8"),
    "4": ("Europe/London",     "GMT  — UK / London                UTC+0"),
    "5": ("Europe/Berlin",     "CET  — Central European Time      UTC+1"),
    "6": ("Asia/Singapore",    "SGT  — Singapore / HK             UTC+8"),
    "7": ("Australia/Sydney",  "AEDT — Australia Eastern          UTC+11"),
    "8": ("UTC",               "UTC  — Coordinated Universal Time UTC+0"),
}

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]


class SetupWizard:
    """Interactive first-time / reconfigure wizard."""

    def run(self, force: bool = False) -> dict:
        if CALENDAR_CONFIG.exists() and not force:
            existing = self._load_existing()
            console.print(Panel(
                "[bold yellow]Setup already complete.[/bold yellow]\n\n"
                "Run [cyan]python main.py setup --reconfigure[/cyan] to change settings,\n"
                "or [cyan]python main.py config-show[/cyan] to view current config.",
                border_style="yellow",
            ))
            return existing

        console.print(Panel(
            "[bold cyan]LinkedIn Post Agent — First-Time Setup[/bold cyan]\n\n"
            "This wizard configures your content calendar, posting schedule,\n"
            "mode (review vs autopilot), and image generation.\n\n"
            "[dim]Takes about 2 minutes. Settings saved to config/calendar_config.yaml[/dim]",
            border_style="cyan",
            padding=(1, 3),
        ))

        cfg = {}
        cfg["calendar"] = self._ask_calendar()
        cfg["image"]    = self._ask_image()
        cfg["output"]   = {
            "save_calendar_json": True,
            "images_dir": "outputs/images",
            "posts_dir":  "outputs/posts",
        }

        self._confirm_and_save(cfg)
        return cfg

    # ── Calendar & Schedule ───────────────────────────────────────────────────

    def _ask_calendar(self) -> dict:
        console.print()
        console.print(Rule("[bold]Step 1 of 3 — Content Calendar[/bold]"))
        console.print()

        # Period
        console.print("[bold]How far ahead should I generate content?[/bold]")
        console.print("  [cyan]1[/cyan]  1 Week   (~3–5 posts)")
        console.print("  [cyan]2[/cyan]  1 Month  (~12–20 posts)  [dim](recommended)[/dim]")
        console.print("  [cyan]3[/cyan]  3 Months (~36–60 posts)")
        period_map = {"1": "1_week", "2": "1_month", "3": "3_months"}
        period_choice = Prompt.ask("\nCalendar period", choices=["1","2","3"], default="2")
        period = period_map[period_choice]

        # Posts per week
        console.print()
        console.print("[bold]How many posts per week?[/bold]")
        console.print("  [cyan]1[/cyan]  1x  (e.g. Wednesday only)")
        console.print("  [cyan]2[/cyan]  2x  (e.g. Tuesday + Thursday)")
        console.print("  [cyan]3[/cyan]  3x  (Mon / Wed / Fri)  [dim](recommended)[/dim]")
        console.print("  [cyan]4[/cyan]  5x  (every weekday)")
        freq_map = {
            "1": (1, [2]),
            "2": (2, [1, 3]),
            "3": (3, [0, 2, 4]),
            "4": (5, [0, 1, 2, 3, 4]),
        }
        freq_choice = Prompt.ask("Frequency", choices=["1","2","3","4"], default="3")
        posts_per_week, posting_days = freq_map[freq_choice]

        # Time
        console.print()
        console.print("[bold]Preferred posting time? (24h format, e.g. 17:30)[/bold]")
        console.print("[dim]Best engagement windows: 07:30–09:00 or 17:00–19:00[/dim]")
        while True:
            time_str = Prompt.ask("Time (HH:MM)", default="17:30")
            if self._valid_time(time_str):
                break
            console.print("[red]Invalid format. Use HH:MM (e.g. 17:30)[/red]")

        # Timezone
        console.print()
        console.print("[bold]Your timezone?[/bold]")
        for k, (tz, label) in TIMEZONES.items():
            console.print(f"  [cyan]{k}[/cyan]  {label}")
        tz_choice = Prompt.ask("\nTimezone", choices=list(TIMEZONES.keys()), default="1")
        timezone = TIMEZONES[tz_choice][0]

        # Mode
        console.print()
        console.print(Rule("[bold]Step 2 of 3 — Posting Mode[/bold]"))
        console.print()
        console.print("[bold]How should the agent handle post approval?[/bold]\n")
        console.print("  [cyan]1[/cyan]  [bold]Review Mode[/bold]  — You review and approve each post before it goes live")
        console.print("         Best for: maintaining full editorial control\n")
        console.print("  [cyan]2[/cyan]  [bold]Autopilot Mode[/bold] — Agent generates, schedules, and posts automatically")
        console.print("         Best for: hands-off content generation (set and forget)\n")
        mode_map = {"1": "review", "2": "autopilot"}
        mode_choice = Prompt.ask("Mode", choices=["1","2"], default="1")
        mode = mode_map[mode_choice]

        days_display = ", ".join(DAY_NAMES[d] for d in posting_days)
        console.print(Panel(
            f"[green]Calendar configured:[/green]\n"
            f"  Period        : {period.replace('_', ' ').title()}\n"
            f"  Frequency     : {posts_per_week}x / week ({days_display})\n"
            f"  Posting time  : {time_str} {timezone}\n"
            f"  Mode          : {mode.title()}",
            border_style="green",
        ))

        return {
            "period": period,
            "posts_per_week": posts_per_week,
            "posting_days": posting_days,
            "schedule": {"time": time_str, "timezone": timezone},
            "mode": mode,
        }

    # ── Image Generation ──────────────────────────────────────────────────────

    def _ask_image(self) -> dict:
        console.print()
        console.print(Rule("[bold]Step 3 of 3 — Image Generation[/bold]"))
        console.print()

        enabled = Confirm.ask(
            "[bold]Generate AI images for each post?[/bold]\n"
            "  [dim]Produces professional editorial-style photos (non-AI-looking)[/dim]",
            default=True,
        )

        if not enabled:
            return {"enabled": False, "provider": "none"}

        console.print()
        console.print("[bold]Image generation provider?[/bold]")
        console.print("  [cyan]1[/cyan]  [bold]DALL-E 3[/bold] (OpenAI)      — High quality, needs OPENAI_API_KEY  [dim](recommended)[/dim]")
        console.print("  [cyan]2[/cyan]  [bold]Stable Diffusion 3[/bold]     — Needs STABILITY_API_KEY")
        console.print("  [cyan]3[/cyan]  [bold]Flux Pro[/bold] (Replicate)   — Best realism, needs REPLICATE_API_KEY")

        prov_map = {"1": "openai", "2": "stability", "3": "replicate"}
        prov_choice = Prompt.ask("Provider", choices=["1","2","3"], default="1")
        provider = prov_map[prov_choice]

        console.print(Panel(
            f"[green]Image generation configured:[/green]\n"
            f"  Provider : {provider}\n"
            f"  Style    : Professional editorial photography\n"
            f"  Format   : 1792x1024 landscape (LinkedIn optimised)",
            border_style="green",
        ))

        return {
            "enabled": True,
            "provider": provider,
            "openai":    {"model": "dall-e-3", "size": "1792x1024", "quality": "hd"},
            "stability": {"model": "sd3-large", "aspect_ratio": "16:9"},
            "replicate": {"model": "black-forest-labs/flux-pro"},
            "style_guidance": (
                "Professional editorial photograph. Shot on Sony A7R IV, 35mm f/1.8 lens. "
                "Cinematic colour grading. Natural or controlled studio lighting. "
                "Shallow depth of field. Modern tech office or data-centre environment. "
                "No text, no watermarks, no UI overlays. Not a stock photo. Photorealistic."
            ),
        }

    # ── Save & Summary ────────────────────────────────────────────────────────

    def _confirm_and_save(self, cfg: dict):
        console.print()
        if Confirm.ask("[bold]Save this configuration?[/bold]", default=True):
            CALENDAR_CONFIG.parent.mkdir(exist_ok=True)
            with open(CALENDAR_CONFIG, "w", encoding="utf-8") as f:
                yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
            console.print(f"\n[green]Saved to {CALENDAR_CONFIG}[/green]")
            console.print("\n[bold]You're all set! Run:[/bold]")
            console.print("  [cyan]python main.py generate-calendar[/cyan]  — generate all posts for your period")
            console.print("  [cyan]python main.py run[/cyan]                 — generate a single post")
        else:
            console.print("[yellow]Setup cancelled — nothing saved.[/yellow]")

    def _valid_time(self, t: str) -> bool:
        try:
            h, m = t.split(":")
            return 0 <= int(h) <= 23 and 0 <= int(m) <= 59
        except Exception:
            return False

    def _load_existing(self) -> dict:
        with open(CALENDAR_CONFIG, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    def show_config(self):
        """Pretty-print the current calendar config."""
        if not CALENDAR_CONFIG.exists():
            console.print("[yellow]No config found. Run: python main.py setup[/yellow]")
            return
        cfg = self._load_existing()
        cal = cfg.get("calendar", {})
        img = cfg.get("image", {})
        sched = cal.get("schedule", {})
        days  = [DAY_NAMES[d] for d in cal.get("posting_days", [])]

        table = Table(title="Current Calendar Config", border_style="cyan")
        table.add_column("Setting", style="bold")
        table.add_column("Value")

        table.add_row("Period",        cal.get("period", "").replace("_", " ").title())
        table.add_row("Frequency",     f"{cal.get('posts_per_week', '?')}x / week ({', '.join(days)})")
        table.add_row("Posting time",  f"{sched.get('time', '?')} {sched.get('timezone', '')}")
        table.add_row("Mode",          cal.get("mode", "?").title())
        table.add_row("Images",        f"{'Enabled' if img.get('enabled') else 'Disabled'} ({img.get('provider', 'none')})")
        console.print(table)
