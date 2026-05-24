"""
Post Scheduler — full lifecycle state machine with LinkedIn native scheduling.

States:  draft → reviewed → approved → scheduled → published
                                                  → failed
                                     → cancelled

Deduplication: content_hash (SHA-256 prefix) prevents the same post being
scheduled or published more than once (Step 7).

Posts scheduled via LinkedIn's native API appear at linkedin.com/content/scheduled/.
No local daemon or Windows Task Scheduler required.
"""
import hashlib
import uuid
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.table import Table
from sqlalchemy import Column, DateTime, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

console = Console()

DB_PATH = Path(__file__).parent.parent.parent.parent / "outputs" / "scheduler.db"
DB_URL  = f"sqlite:///{DB_PATH}"


# ── SQLAlchemy Models ─────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    pass


class ScheduledPost(Base):
    __tablename__ = "scheduled_posts"

    id               = Column(String, primary_key=True, default=lambda: str(uuid.uuid4())[:8])
    content          = Column(Text, nullable=False)
    content_hash     = Column(String, nullable=True, unique=False)   # SHA-256 prefix for dedup
    scheduled_time   = Column(DateTime, nullable=True)
    status           = Column(String, default="draft")
    # Status lifecycle:
    #   draft      — generated, saved as file, not yet reviewed
    #   reviewed   — user manually reviewed the content
    #   approved   — eligible for posting to LinkedIn
    #   scheduled  — sent to LinkedIn native scheduler
    #   published  — posted immediately (post_now)
    #   failed     — LinkedIn API rejected it
    #   cancelled  — user cancelled before posting
    review_folder    = Column(String, nullable=True)    # which date folder it came from
    created_at       = Column(DateTime, default=datetime.utcnow)
    reviewed_at      = Column(DateTime, nullable=True)
    published_at     = Column(DateTime, nullable=True)
    error_message    = Column(Text, nullable=True)
    linkedin_post_id = Column(String, nullable=True)
    linkedin_url     = Column(String, nullable=True)
    topic            = Column(String, nullable=True)
    series_part      = Column(String, nullable=True)
    word_count       = Column(String, nullable=True)


def _hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


# ── Scheduler ─────────────────────────────────────────────────────────────────

class PostScheduler:

    def __init__(self):
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._engine = create_engine(DB_URL)
        Base.metadata.create_all(self._engine)

    # ── Registration (called by mark-reviewed) ────────────────────────────────

    def register_draft(
        self,
        content: str,
        review_folder: str,
        topic: str = "",
        series_part: str = "",
        word_count: int = 0,
    ) -> tuple[str, bool]:
        """
        Register a reviewed draft in the DB. Returns (post_id, is_duplicate).
        Duplicates (same content_hash with status != draft) are rejected.
        """
        h = _hash(content)

        with Session(self._engine) as session:
            existing = session.query(ScheduledPost).filter_by(content_hash=h).first()
            if existing and existing.status not in ("draft",):
                return existing.id, True  # duplicate

            post_id = str(uuid.uuid4())[:8]
            record = ScheduledPost(
                id=post_id,
                content=content,
                content_hash=h,
                status="reviewed",
                review_folder=review_folder,
                reviewed_at=datetime.utcnow(),
                topic=topic,
                series_part=series_part,
                word_count=str(word_count),
            )
            session.add(record)
            session.commit()

        return post_id, False

    # ── Scheduling (Step 6) ───────────────────────────────────────────────────

    def schedule_post(self, content: str, scheduled_time: datetime, post_id: str = None) -> str:
        """
        Schedule a reviewed post via LinkedIn's native scheduler.
        Rejects duplicates (same hash already scheduled or published).
        """
        from linkedin_post_agent.tools.linkedin_tool import LinkedInTool

        h = _hash(content)
        self._check_duplicate(h)

        if post_id is None:
            post_id = str(uuid.uuid4())[:8]

        with Session(self._engine) as session:
            record = session.get(ScheduledPost, post_id)
            if record is None:
                record = ScheduledPost(
                    id=post_id, content=content, content_hash=h,
                    scheduled_time=scheduled_time, status="approved",
                )
                session.add(record)
            else:
                record.scheduled_time = scheduled_time
                record.status = "approved"
            session.commit()

        tool = LinkedInTool()
        result = tool.post(content, scheduled_time=scheduled_time)

        with Session(self._engine) as session:
            record = session.get(ScheduledPost, post_id)
            if result.get("success"):
                record.status = "scheduled"
                record.linkedin_post_id = result.get("post_id", "")
                record.linkedin_url = result.get("url", "")
                session.commit()
                console.print(
                    f"\n[bold green]Scheduled on LinkedIn native calendar![/bold green]\n"
                    f"  Local ID     : [cyan]{post_id}[/cyan]\n"
                    f"  LinkedIn ID  : [cyan]{result.get('post_id', 'N/A')}[/cyan]\n"
                    f"  Publish time : {scheduled_time.strftime('%A %d %B %Y at %H:%M')}\n"
                    f"  Calendar     : {result.get('url', '')}\n"
                )
            else:
                record.status = "failed"
                record.error_message = result.get("error", "Unknown")
                session.commit()
                from rich.markup import escape
                console.print(f"[red]Scheduling failed: {escape(str(result.get('error', 'Unknown')))}[/red]")

        return post_id

    def post_now(self, content: str, post_id: str = None) -> str:
        """Publish immediately. Rejects duplicates."""
        from linkedin_post_agent.tools.linkedin_tool import LinkedInTool

        h = _hash(content)
        self._check_duplicate(h)

        if post_id is None:
            post_id = str(uuid.uuid4())[:8]

        with Session(self._engine) as session:
            record = session.get(ScheduledPost, post_id)
            if record is None:
                record = ScheduledPost(
                    id=post_id, content=content, content_hash=h, status="approved"
                )
                session.add(record)
            session.commit()

        tool = LinkedInTool()
        result = tool.post(content)

        with Session(self._engine) as session:
            record = session.get(ScheduledPost, post_id)
            if result.get("success"):
                record.status = "published"
                record.published_at = datetime.utcnow()
                record.linkedin_post_id = result.get("post_id", "")
                record.linkedin_url = result.get("url", "")
                session.commit()
                console.print(
                    f"\n[bold green]Published to LinkedIn![/bold green]\n"
                    f"  Post ID : [cyan]{result.get('post_id', 'N/A')}[/cyan]\n"
                    f"  URL     : {result.get('url', '')}\n"
                )
            else:
                record.status = "failed"
                record.error_message = result.get("error", "Unknown")
                session.commit()
                console.print(f"[red]Publish failed: {result.get('error')}[/red]")

        return post_id

    # ── Deduplication (Step 7) ────────────────────────────────────────────────

    def _check_duplicate(self, content_hash: str):
        """Raise if this content was already scheduled or published."""
        with Session(self._engine) as session:
            existing = session.query(ScheduledPost).filter_by(content_hash=content_hash).first()
            if existing and existing.status in ("scheduled", "published"):
                raise ValueError(
                    f"DUPLICATE REJECTED: This content was already "
                    f"{existing.status} (ID: {existing.id}). "
                    "The same post cannot be published twice (Step 7 dedup rule)."
                )

    # ── Listing ───────────────────────────────────────────────────────────────

    def list_scheduled(self, status_filter: str = None):
        """Print all tracked posts as a Rich table."""
        with Session(self._engine) as session:
            q = session.query(ScheduledPost)
            if status_filter:
                q = q.filter(ScheduledPost.status == status_filter)
            posts = q.order_by(ScheduledPost.created_at.desc()).all()

        if not posts:
            console.print("[dim]No posts found.[/dim]")
            return

        table = Table(title="LinkedIn Posts — Lifecycle Tracker", border_style="cyan")
        table.add_column("ID", style="bold cyan")
        table.add_column("Status")
        table.add_column("Topic", max_width=30)
        table.add_column("Series")
        table.add_column("Schedule / Published")
        table.add_column("Words")
        table.add_column("Preview", max_width=40)

        status_colors = {
            "draft":     "dim",
            "reviewed":  "yellow",
            "approved":  "blue",
            "scheduled": "cyan",
            "published": "green",
            "failed":    "red",
            "cancelled": "dim",
        }

        for p in posts:
            color = status_colors.get(p.status, "white")
            time_str = (
                p.scheduled_time.strftime("%Y-%m-%d %H:%M")
                if p.scheduled_time
                else (p.published_at.strftime("%Y-%m-%d %H:%M") if p.published_at else "—")
            )
            table.add_row(
                p.id,
                f"[{color}]{p.status}[/{color}]",
                (p.topic or "")[:30],
                p.series_part or "—",
                time_str,
                p.word_count or "—",
                p.content[:50].replace("\n", " ") + "…",
            )

        console.print(table)
        console.print(
            "[dim]LinkedIn calendar: https://www.linkedin.com/content/scheduled/[/dim]"
        )

    # ── Cancel ────────────────────────────────────────────────────────────────

    def cancel_post(self, post_id: str) -> bool:
        with Session(self._engine) as session:
            post = session.get(ScheduledPost, post_id)
            if not post or post.status not in ("draft", "reviewed", "approved", "scheduled"):
                return False
            post.status = "cancelled"
            session.commit()

        console.print(
            f"[yellow]Post {post_id} cancelled.[/yellow]\n"
            "[dim]If already sent to LinkedIn, also cancel at: "
            "https://www.linkedin.com/content/scheduled/[/dim]"
        )
        return True
