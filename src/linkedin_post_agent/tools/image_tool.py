"""
Image Generation Tool — produces professional, non-AI-looking editorial images.
Supports: DALL-E 3 (OpenAI), Stable Diffusion 3 (Stability AI), Flux Pro (Replicate).

Image prompt strategy: ask Claude to craft a photorealistic photography brief,
then pass that to the image model — avoids the typical "AI art" look.
"""
import os
import uuid
import requests
from pathlib import Path
from typing import Optional

from rich.console import Console

console = Console()
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent


class ImageGenerationTool:
    """Multi-provider image generation optimised for LinkedIn posts."""

    def __init__(self, cfg: dict = None):
        self.cfg = cfg or {}
        self.provider = self.cfg.get("provider", os.getenv("IMAGE_PROVIDER", "openai"))
        self.enabled  = self.cfg.get("enabled", True)
        self.style_guidance = self.cfg.get(
            "style_guidance",
            "Professional editorial photograph. Shot on Sony A7R IV, 35mm f/1.8 lens. "
            "Cinematic colour grading. Natural or studio lighting. Shallow depth of field. "
            "Modern tech office or data-centre environment. "
            "No text, no watermarks. Not a stock photo. Photorealistic."
        )

    # ── Public API ────────────────────────────────────────────────────────────

    def generate(self, post_content: str, topic: str = "") -> Optional[str]:
        """
        Generate an image for the post.
        Returns the local file path of the saved image, or None on failure.
        """
        if not self.enabled or self.provider == "none":
            return None

        console.print(f"[dim]  Generating image via {self.provider}...[/dim]")

        prompt = self._craft_photography_prompt(post_content, topic)
        console.print(f"[dim]  Image prompt: {prompt[:120]}...[/dim]")

        try:
            if self.provider == "openai":
                return self._dalle3(prompt)
            elif self.provider == "stability":
                return self._stability(prompt)
            elif self.provider == "replicate":
                return self._replicate_flux(prompt)
            else:
                console.print(f"[yellow]Unknown image provider: {self.provider}[/yellow]")
                return None
        except Exception as e:
            console.print(f"[red]Image generation failed ({self.provider}): {e}[/red]")
            return None

    # ── Prompt Crafting ───────────────────────────────────────────────────────

    def _craft_photography_prompt(self, post_content: str, topic: str) -> str:
        """
        Use Claude to extract the core visual concept from the post and craft
        a cinematic photography brief — not an 'AI art' prompt.
        """
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

            instruction = f"""You are a commercial photography art director briefing a photographer.

Based on the LinkedIn post below, write a SINGLE concise photography brief (max 80 words).
The brief must:
- Describe a REAL, PLAUSIBLE scene (not abstract or surreal)
- Feature a person or environment that fits a global investment bank / senior tech leader context
- Avoid text, logos, screens with legible content, or overly literal metaphors
- Sound like a shoot brief, not a stock photo caption

Post topic: {topic}
Post excerpt: {post_content[:300]}

Reply with ONLY the photography brief. No explanation."""

            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=200,
                messages=[{"role": "user", "content": instruction}],
            )
            scene_brief = response.content[0].text.strip()

        except Exception:
            # Fallback: use a generic but effective prompt
            scene_brief = (
                "A focused senior engineer at a standing desk in a modern glass-walled office, "
                "reviewing architecture diagrams on a large monitor, warm late-afternoon sunlight "
                "streaming in, bokeh city skyline in background."
            )

        return f"{scene_brief} {self.style_guidance}"

    # ── Providers ─────────────────────────────────────────────────────────────

    def _dalle3(self, prompt: str) -> Optional[str]:
        from openai import OpenAI
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key or api_key == "sk-...":
            console.print("[yellow]OPENAI_API_KEY not set — skipping image.[/yellow]")
            return None

        prov_cfg = self.cfg.get("openai", {})
        client = OpenAI(api_key=api_key)
        response = client.images.generate(
            model=prov_cfg.get("model", "dall-e-3"),
            prompt=prompt,
            size=prov_cfg.get("size", "1792x1024"),
            quality=prov_cfg.get("quality", "hd"),
            n=1,
        )
        image_url = response.data[0].url
        revised   = getattr(response.data[0], "revised_prompt", "")
        if revised:
            console.print(f"[dim]  DALL-E revised prompt: {revised[:100]}...[/dim]")

        return self._download(image_url, "dalle3")

    def _stability(self, prompt: str) -> Optional[str]:
        api_key = os.getenv("STABILITY_API_KEY")
        if not api_key:
            console.print("[yellow]STABILITY_API_KEY not set — skipping image.[/yellow]")
            return None

        prov_cfg = self.cfg.get("stability", {})
        response = requests.post(
            "https://api.stability.ai/v2beta/stable-image/generate/sd3",
            headers={
                "authorization": f"Bearer {api_key}",
                "accept": "image/*",
            },
            data={
                "prompt": prompt,
                "model": prov_cfg.get("model", "sd3-large"),
                "aspect_ratio": prov_cfg.get("aspect_ratio", "16:9"),
                "output_format": "png",
            },
            files={"none": ""},
            timeout=60,
        )
        if not response.ok:
            raise RuntimeError(f"Stability API {response.status_code}: {response.text[:200]}")

        return self._save_bytes(response.content, "stability", "png")

    def _replicate_flux(self, prompt: str) -> Optional[str]:
        api_key = os.getenv("REPLICATE_API_KEY")
        if not api_key:
            console.print("[yellow]REPLICATE_API_KEY not set — skipping image.[/yellow]")
            return None

        import time
        prov_cfg = self.cfg.get("replicate", {})
        model    = prov_cfg.get("model", "black-forest-labs/flux-pro")

        # Replicate prediction API
        resp = requests.post(
            f"https://api.replicate.com/v1/models/{model}/predictions",
            headers={"Authorization": f"Token {api_key}", "Content-Type": "application/json"},
            json={"input": {"prompt": prompt, "aspect_ratio": "16:9", "output_format": "png"}},
            timeout=30,
        )
        if not resp.ok:
            raise RuntimeError(f"Replicate API error: {resp.text[:200]}")

        prediction_url = resp.json()["urls"]["get"]

        # Poll for completion
        for _ in range(60):
            time.sleep(3)
            poll = requests.get(
                prediction_url,
                headers={"Authorization": f"Token {api_key}"},
                timeout=10,
            )
            data = poll.json()
            if data["status"] == "succeeded":
                image_url = data["output"][0] if isinstance(data["output"], list) else data["output"]
                return self._download(image_url, "flux")
            elif data["status"] == "failed":
                raise RuntimeError(f"Replicate prediction failed: {data.get('error')}")

        raise RuntimeError("Replicate prediction timed out after 3 minutes.")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _download(self, url: str, provider: str) -> str:
        response = requests.get(url, timeout=60)
        response.raise_for_status()
        ext = "jpg" if "jpeg" in response.headers.get("content-type", "") else "png"
        return self._save_bytes(response.content, provider, ext)

    def _save_bytes(self, data: bytes, provider: str, ext: str) -> str:
        img_dir = PROJECT_ROOT / "outputs" / "images"
        img_dir.mkdir(parents=True, exist_ok=True)
        filename = img_dir / f"post_{provider}_{uuid.uuid4().hex[:8]}.{ext}"
        filename.write_bytes(data)
        console.print(f"[green]Image saved: {filename}[/green]")
        return str(filename)
