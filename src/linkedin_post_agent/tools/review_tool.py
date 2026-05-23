"""
Human Review Tool — interactive CLI review loop with Rich UI.
Shows the draft post and collects approve / revise / quit decisions.
"""
from typing import Tuple, Optional

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich.rule import Rule
from rich.text import Text

console = Console()


class HumanReviewTool:
    """Presents a LinkedIn post draft for human review and collects feedback."""

    def review(self, post_content: str, iteration: int = 1) -> Tuple[str, Optional[str]]:
        """
        Display the post and prompt for a decision.

        Returns:
            (action, feedback) where action is "approve" | "revise" | "quit"
            and feedback is the revision instructions (or None).
        """
        console.print()
        console.print(Rule(f"[bold yellow]Draft Post — Iteration {iteration}[/bold yellow]"))
        console.print()

        # Render post content (show as plain text, respect newlines)
        panel_content = Text(post_content)
        console.print(Panel(
            panel_content,
            border_style="yellow",
            padding=(1, 3),
            title="[bold]LinkedIn Post Preview[/bold]",
            subtitle=f"[dim]{len(post_content)} characters · ~{len(post_content.split())} words[/dim]",
        ))
        console.print()

        # Char count warning
        if len(post_content) > 3000:
            console.print(
                "[yellow]WARNING: Post exceeds LinkedIn's 3000-character soft limit "
                "-- consider trimming.[/yellow]\n"
            )

        # Decision prompt
        console.print("[bold]What would you like to do?[/bold]")
        console.print("  [green]approve[/green]  — Publish or schedule this post")
        console.print("  [yellow]revise[/yellow]   — Send back with your feedback")
        console.print("  [red]quit[/red]     — Discard and exit")
        console.print()

        action = Prompt.ask(
            "Your choice",
            choices=["approve", "revise", "quit"],
            default="approve",
        )

        feedback = None
        if action == "revise":
            console.print()
            feedback = Prompt.ask(
                "[bold yellow]Feedback / instructions for the rewrite[/bold yellow]"
            )
            if not feedback.strip():
                console.print("[dim]No feedback given — rerunning with no changes.[/dim]")
                feedback = "Please improve the post generally for clarity and engagement."

        return action, feedback

    def confirm_schedule(self) -> Tuple[bool, Optional[str]]:
        """Ask whether to post now or schedule for later. Returns (post_now, schedule_str)."""
        console.print()
        post_now = Confirm.ask(
            "[bold]Post to LinkedIn immediately?[/bold]", default=True
        )

        if post_now:
            return True, None

        console.print(
            "[dim]Enter schedule time in format: YYYY-MM-DD HH:MM  "
            "(e.g., 2026-06-01 09:00)[/dim]"
        )
        schedule_str = Prompt.ask(
            "Schedule time",
            default="",
            show_default=False,
        )
        return False, schedule_str.strip() or None
