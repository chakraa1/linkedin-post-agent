"""
DEPRECATED — this script was used by Windows Task Scheduler for local daemon scheduling.
Scheduling is now done via LinkedIn's native scheduler API (lifecycleState: SCHEDULED).
Use: python main.py approve-post <file> --schedule "YYYY-MM-DD HH:MM"
"""
import sys
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent / "src"))

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from linkedin_post_agent.utils.scheduler import ScheduledPost, Base
from linkedin_post_agent.tools.linkedin_tool import LinkedInTool
from datetime import datetime

DB_PATH = Path(__file__).parent / "outputs" / "scheduler.db"
DB_URL  = f"sqlite:///{DB_PATH}"


def publish(post_id: str):
    engine = create_engine(DB_URL)
    Base.metadata.create_all(engine)
    _Session = sessionmaker(bind=engine)

    with Session(engine) as session:
        post = session.get(ScheduledPost, post_id)
        if not post:
            print(f"Post {post_id} not found.")
            sys.exit(1)
        if post.status != "pending":
            print(f"Post {post_id} is already {post.status} — skipping.")
            sys.exit(0)

        print(f"Publishing post {post_id} to LinkedIn...")
        tool = LinkedInTool()
        result = tool.post(post.content)

        if result.get("success"):
            post.status = "published"
            post.published_at = datetime.utcnow()
            post.linkedin_post_id = result.get("post_id", "")
            session.commit()
            print(f"Published. LinkedIn post ID: {result.get('post_id')}")
            print(f"URL: {result.get('url')}")
        else:
            post.status = "failed"
            post.error_message = result.get("error", "Unknown")
            session.commit()
            print(f"Failed: {result.get('error')}")
            sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python publish_post.py <post_id>")
        sys.exit(1)
    publish(sys.argv[1])
