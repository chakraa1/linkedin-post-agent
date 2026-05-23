"""
Perplexity Research Tool — wraps Perplexity's web-grounded API as a CrewAI tool.
Perplexity's API is OpenAI-compatible; sonar-pro has real-time web access + citations.
"""
import os
from typing import Type

from crewai.tools import BaseTool
from openai import OpenAI
from pydantic import BaseModel, Field
from rich.console import Console

console = Console()


class PerplexitySearchInput(BaseModel):
    query: str = Field(
        description=(
            "The research query. Be specific — include topic, timeframe "
            "(e.g., 'past 2 weeks'), and audience context."
        )
    )


class PerplexityResearchTool(BaseTool):
    name: str = "perplexity_web_research"
    description: str = (
        "Search the live web via Perplexity AI for the latest news, trends, "
        "announcements, and data points on any technology topic. Returns a "
        "research summary with citations. Use this for up-to-date information "
        "that may postdate your training data."
    )
    args_schema: Type[BaseModel] = PerplexitySearchInput

    def _run(self, query: str) -> str:
        api_key = os.getenv("PERPLEXITY_API_KEY")
        if not api_key:
            return (
                "ERROR: PERPLEXITY_API_KEY not set. "
                "Add it to your .env file. Falling back to training knowledge."
            )

        try:
            client = OpenAI(
                api_key=api_key,
                base_url="https://api.perplexity.ai",
            )

            model = os.getenv("PERPLEXITY_MODEL", "sonar-pro")

            console.print(f"[dim]  Perplexity [{model}]: {query[:80]}...[/dim]")

            response = client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a senior technology research analyst. "
                            "Provide factual, citation-backed research on the query. "
                            "Include specific data points, version numbers, dates, and "
                            "URLs where available. Be concise but thorough. "
                            "Structure your response in clear sections."
                        ),
                    },
                    {"role": "user", "content": query},
                ],
                temperature=0.1,
                max_tokens=2048,
            )

            result = response.choices[0].message.content

            # Append citations if present in the response object
            if hasattr(response, "citations") and response.citations:
                citations = "\n\n**Sources:**\n" + "\n".join(
                    f"- {c}" for c in response.citations
                )
                result += citations

            return result

        except Exception as e:
            return f"Perplexity API error: {e}. Falling back to training knowledge."
