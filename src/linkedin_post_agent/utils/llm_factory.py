"""
LLM Factory — creates CrewAI LLM instances from llm_config.yaml.
Supports: Anthropic, OpenAI, Perplexity, Ollama, Groq.
Agent-level overrides can also come from environment variables.
"""
import os
from pathlib import Path
from typing import Optional

import yaml
from crewai import LLM
from rich.console import Console
from rich.table import Table

console = Console()


class LLMFactory:
    """Builds CrewAI LLM instances for named agents from YAML config."""

    # LiteLLM model prefix per provider
    _PREFIXES = {
        "anthropic": "anthropic/",
        "openai": "",
        "perplexity": "openai/",   # Uses OpenAI-compatible endpoint
        "ollama": "ollama/",
        "groq": "groq/",
        "cohere": "cohere/",
        "mistral": "mistral/",
    }

    def __init__(self, config_path: str):
        self._config = self._load(config_path)
        self._providers: dict = self._config.get("providers", {})
        self._mappings: dict = self._config.get("agent_llm_mapping", {})
        self._default: dict = self._config.get(
            "default", {"provider": "anthropic", "model": "claude-sonnet-4-6"}
        )

    def _load(self, path: str) -> dict:
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"LLM config not found: {path}")
        with open(p, "r") as f:
            return yaml.safe_load(f)

    # ── Public API ────────────────────────────────────────────────────────────

    def get_llm(self, agent_name: str) -> LLM:
        """Return a configured CrewAI LLM for the given agent name."""
        mapping = self._resolve_mapping(agent_name)
        provider = mapping["provider"]
        model = mapping["model"]
        return self._build_llm(provider, model)

    def validate(self):
        """Print a table showing each agent's LLM and whether the API key is set."""
        table = Table(title="LLM Configuration", border_style="cyan")
        table.add_column("Agent", style="bold")
        table.add_column("Provider")
        table.add_column("Model")
        table.add_column("API Key", justify="center")

        for agent_name in list(self._mappings.keys()) + ["(default)"]:
            if agent_name == "(default)":
                mapping = self._default
            else:
                mapping = self._mappings[agent_name]

            provider = mapping.get("provider", "anthropic")
            model = mapping.get("model", "?")
            provider_cfg = self._providers.get(provider, {})
            api_key_env = provider_cfg.get("api_key_env", "")

            if not api_key_env:
                key_status = "[dim]n/a[/dim]"
            elif os.getenv(api_key_env):
                key_status = "[green]OK[/green]"
            else:
                key_status = f"[red]MISSING: {api_key_env}[/red]"

            table.add_row(agent_name, provider, model, key_status)

        console.print(table)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _resolve_mapping(self, agent_name: str) -> dict:
        """Merge YAML mapping with optional env var overrides."""
        base = dict(self._mappings.get(agent_name, self._default))

        # Allow per-agent env overrides like RESEARCH_AGENT_PROVIDER=openai
        env_key = agent_name.upper().replace("-", "_")
        provider_override = os.getenv(f"{env_key}_PROVIDER")
        model_override = os.getenv(f"{env_key}_MODEL")
        if provider_override:
            base["provider"] = provider_override
        if model_override:
            base["model"] = model_override

        return base

    def _build_llm(self, provider: str, model: str) -> LLM:
        provider_cfg = self._providers.get(provider, {})
        api_key_env = provider_cfg.get("api_key_env", "")
        api_key = os.getenv(api_key_env) if api_key_env else None
        base_url = provider_cfg.get("base_url")

        prefix = self._PREFIXES.get(provider, "")
        model_str = f"{prefix}{model}"

        kwargs: dict = {"model": model_str}
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["base_url"] = base_url

        console.print(f"[dim]  LLM → {model_str}[/dim]")
        return LLM(**kwargs)
