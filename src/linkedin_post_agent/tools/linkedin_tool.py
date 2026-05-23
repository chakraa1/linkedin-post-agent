"""
LinkedIn Tool — OAuth 2.0 authentication and post publishing via LinkedIn API v2.

Setup:
  1. Create a LinkedIn Developer App at https://developer.linkedin.com
  2. Add products: "Sign In with LinkedIn using OpenID Connect" + "Share on LinkedIn"
  3. Set redirect URI: http://localhost:8080/callback
  4. Copy Client ID and Client Secret to .env
  5. Run: python main.py auth
"""
import http.server
import json
import os
import threading
import time
import urllib.parse
import webbrowser
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

import requests
from rich.console import Console
from rich.panel import Panel

console = Console()

TOKEN_FILE = Path(__file__).parent.parent.parent.parent / ".linkedin_token.json"

LINKEDIN_AUTH_URL = "https://www.linkedin.com/oauth/v2/authorization"
LINKEDIN_TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
LINKEDIN_API_BASE = "https://api.linkedin.com/v2"
REDIRECT_URI = "http://localhost:8080/callback"
SCOPES = "openid profile w_member_social"  # r_liteprofile is deprecated; use openid+profile


class _OAuthCallbackHandler(http.server.BaseHTTPRequestHandler):
    """Minimal HTTP handler to capture the OAuth authorization code."""

    auth_code: Optional[str] = None
    error: Optional[str] = None

    def do_GET(self):
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if "code" in params:
            _OAuthCallbackHandler.auth_code = params["code"][0]
            body = b"<h2>Authorization successful!</h2><p>You can close this tab.</p>"
        elif "error" in params:
            _OAuthCallbackHandler.error = params.get("error_description", ["Unknown"])[0]
            body = b"<h2>Authorization failed.</h2><p>Check the terminal.</p>"
        else:
            body = b"<h2>Waiting...</h2>"

        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body)
        threading.Thread(target=self.server.shutdown, daemon=True).start()

    def log_message(self, *args):
        pass  # Suppress default access log spam


class LinkedInTool:
    """Handles LinkedIn OAuth and post publishing."""

    def __init__(self):
        self.client_id = os.getenv("LINKEDIN_CLIENT_ID", "")
        self.client_secret = os.getenv("LINKEDIN_CLIENT_SECRET", "")
        self._token_data: Optional[dict] = self._load_token()

    # ── Token Management ──────────────────────────────────────────────────────

    def _load_token(self) -> Optional[dict]:
        if TOKEN_FILE.exists():
            try:
                return json.loads(TOKEN_FILE.read_text())
            except Exception:
                return None
        return None

    def _save_token(self, data: dict):
        TOKEN_FILE.write_text(json.dumps(data, indent=2))
        console.print(f"[dim]Token saved to {TOKEN_FILE}[/dim]")

    def _get_access_token(self) -> Optional[str]:
        # Prefer env var (e.g., for deployment)
        env_token = os.getenv("LINKEDIN_ACCESS_TOKEN")
        if env_token:
            return env_token

        if self._token_data:
            expires_at = self._token_data.get("expires_at", 0)
            if time.time() < expires_at - 300:  # 5-min buffer
                return self._token_data["access_token"]
            console.print("[yellow]LinkedIn token expired. Run: python main.py auth[/yellow]")

        return None

    def _get_person_urn(self) -> Optional[str]:
        env_urn = os.getenv("LINKEDIN_PERSON_URN")
        if env_urn:
            return env_urn
        if self._token_data:
            return self._token_data.get("person_urn")
        return None

    # ── OAuth Flow ────────────────────────────────────────────────────────────

    def authenticate(self):
        """Run OAuth 2.0 Authorization Code flow — manual paste mode (no localhost server needed)."""
        if not self.client_id or not self.client_secret:
            console.print(
                "[red]LINKEDIN_CLIENT_ID and LINKEDIN_CLIENT_SECRET must be set in .env[/red]\n"
                "Create an app at https://developer.linkedin.com"
            )
            return

        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": REDIRECT_URI,
            "scope": SCOPES,
            "state": "linkedin_post_agent",
        }
        auth_url = f"{LINKEDIN_AUTH_URL}?{urlencode(params)}"

        console.print(Panel(
            "[bold cyan]LinkedIn OAuth — Step by Step[/bold cyan]\n\n"
            "[bold]Step 1:[/bold] Open this URL in your browser:\n\n"
            f"  {auth_url}\n\n"
            "[bold]Step 2:[/bold] Log in to LinkedIn and click [bold green]Allow[/bold green].\n\n"
            "[bold]Step 3:[/bold] The browser will fail to load (localhost refused).\n"
            "  That is expected. Copy the full URL from the address bar.\n"
            "  It starts with: [dim]http://localhost:8080/callback?code=...[/dim]\n\n"
            "[bold]Step 4:[/bold] Paste that full URL into this terminal when prompted.",
            border_style="cyan",
            padding=(1, 2),
        ))

        webbrowser.open(auth_url)

        try:
            callback_url = input("\nPaste the redirect URL from your browser address bar:\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[yellow]Cancelled.[/yellow]")
            return

        parsed = urllib.parse.urlparse(callback_url)
        query  = urllib.parse.parse_qs(parsed.query)

        if "error" in query:
            desc = query.get("error_description", ["Unknown error"])[0]
            console.print(f"[red]Authorization failed: {desc}[/red]")
            return

        auth_code = query.get("code", [None])[0]
        if not auth_code:
            console.print(
                "[red]No authorization code found in that URL.[/red]\n"
                "Make sure you copied the full address bar URL after clicking Allow."
            )
            return

        console.print("\n[green]Code captured. Exchanging for access token...[/green]")
        token_resp = requests.post(
            LINKEDIN_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": auth_code,
                "redirect_uri": REDIRECT_URI,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
        )

        if not token_resp.ok:
            console.print(f"[red]Token exchange failed: {token_resp.text}[/red]")
            return

        token_data = token_resp.json()
        access_token = token_data["access_token"]
        expires_in = token_data.get("expires_in", 5184000)  # 60 days default

        # Fetch person URN
        person_urn = self._fetch_person_urn(access_token)
        if not person_urn:
            console.print("[red]Could not fetch LinkedIn profile info.[/red]")
            return

        # Persist
        self._token_data = {
            "access_token": access_token,
            "expires_at": time.time() + expires_in,
            "expires_in": expires_in,
            "person_urn": person_urn,
        }
        self._save_token(self._token_data)

        console.print(Panel(
            f"[bold green]Authentication successful![/bold green]\n\n"
            f"Person URN: [cyan]{person_urn}[/cyan]\n"
            f"Token expires: {datetime.now() + timedelta(seconds=expires_in):%Y-%m-%d}\n\n"
            f"Add to .env:\n"
            f"[dim]LINKEDIN_ACCESS_TOKEN={access_token[:20]}...[/dim]\n"
            f"[dim]LINKEDIN_PERSON_URN={person_urn}[/dim]",
            border_style="green",
        ))

    def _fetch_person_urn(self, access_token: str) -> Optional[str]:
        # OpenID Connect userinfo endpoint (works with openid+profile scopes)
        resp = requests.get(
            "https://api.linkedin.com/v2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        if resp.ok:
            data = resp.json()
            sub = data.get("sub")  # sub is the member ID
            if sub:
                return f"urn:li:person:{sub}"
        # Fallback: legacy /v2/me endpoint
        resp2 = requests.get(
            f"{LINKEDIN_API_BASE}/me",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        if resp2.ok:
            person_id = resp2.json().get("id")
            if person_id:
                return f"urn:li:person:{person_id}"
        console.print(f"[yellow]Profile fetch warning: {resp.text}[/yellow]")
        return None

    # ── Posting ───────────────────────────────────────────────────────────────

    def post(self, content: str, scheduled_time: Optional[datetime] = None) -> dict:
        """Publish or schedule a post on LinkedIn.

        Pass scheduled_time to use LinkedIn's native scheduler — the post will
        appear in linkedin.com/content/scheduled/ and can be managed there.
        Omit scheduled_time to publish immediately.
        """
        access_token = self._get_access_token()
        person_urn = self._get_person_urn()

        if not access_token:
            return {
                "success": False,
                "error": "No LinkedIn access token. Run: python main.py auth",
            }

        if not person_urn:
            return {
                "success": False,
                "error": "No LinkedIn person URN. Run: python main.py auth",
            }

        is_scheduled = scheduled_time is not None
        payload: dict = {
            "author": person_urn,
            "lifecycleState": "SCHEDULED" if is_scheduled else "PUBLISHED",
            "specificContent": {
                "com.linkedin.ugc.ShareContent": {
                    "shareCommentary": {"text": content},
                    "shareMediaCategory": "NONE",
                }
            },
            "visibility": {
                "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"
            },
        }
        if is_scheduled:
            payload["scheduledPublishTime"] = int(scheduled_time.timestamp() * 1000)

        resp = requests.post(
            f"{LINKEDIN_API_BASE}/ugcPosts",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "X-Restli-Protocol-Version": "2.0.0",
            },
            json=payload,
            timeout=30,
        )

        if resp.status_code in (200, 201):
            post_id = resp.headers.get("x-restli-id") or resp.json().get("id", "")
            person_id = person_urn.split(":")[-1]
            url = (
                "https://www.linkedin.com/content/scheduled/"
                if is_scheduled
                else f"https://www.linkedin.com/in/{person_id}/recent-activity/shares/"
            )
            return {
                "success": True,
                "post_id": post_id,
                "url": url,
                "scheduled": is_scheduled,
                "scheduled_time": scheduled_time.isoformat() if is_scheduled else None,
            }

        return {
            "success": False,
            "error": f"HTTP {resp.status_code}: {resp.text[:300]}",
        }

    # ── CrewAI Tool Interface ─────────────────────────────────────────────────

    def _run(self, post_content: str) -> str:
        """Called by CrewAI agents when using this as a tool."""
        result = self.post(post_content)
        if result["success"]:
            return f"Post published. ID: {result['post_id']} | URL: {result['url']}"
        return f"Failed to publish: {result['error']}"
