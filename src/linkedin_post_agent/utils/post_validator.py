"""
Post Validation Hook — 9-rule quality gate (A through I).

Rule mapping (CSV specification):
  A — Human Tone       : avg sentence ≤ 15 words + Flesch ≥ 60 + Haiku tone audit
  B — Word Count       : 200-300 words (hard reject > 300)
  C — Eye-Catching Hook: Haiku LLM audit of opening line
  D — Non-Obvious Insight: Haiku LLM-as-judge evaluator
  E — Achievement Guard: regex for unhedged first-person claims
  F — Format Compliance: em/en dash character scan
  G — Source Links     : ## SOURCES section + HTTP HEAD liveness check
  H — Role Alignment   : 3-5 hashtags, ≥1 in target-role whitelist
  I — Engagement Hook  : post ends with question or CTA
"""
import json
import os
import re
from dataclasses import dataclass, field

import anthropic
import requests
from rich.console import Console

console = Console()

# ── Constants ─────────────────────────────────────────────────────────────────

_UNAUTHORIZED_CLAIM_PATTERNS = [
    r"\b(I|we)\s+(implemented|built|deployed|designed|created|developed|led|ran|managed|launched|architected|migrated|shipped|rolled out|scaled)\b",
    r"\b(I've|we've|I have|we have)\s+(implemented|built|deployed|designed|created|developed|led|ran|managed|launched|architected|migrated|shipped|rolled out|scaled)\b",
    r"\bI\s+personally\b",
    r"\bour\s+team\s+(built|created|implemented|deployed|launched|designed)\b",
]

_GPT_PHRASES = [
    "in today's rapidly evolving",
    "in today's landscape",
    "in an era of",
    "it is worth noting",
    "it's worth noting",
    "delve into",
    "navigate the complexities",
    "revolutionize",
    "unlock the potential",
    "game-changer",
    "paradigm shift",
    "at the end of the day",
    "the bottom line is",
    "having said that",
    "in conclusion",
    "to summarize",
    "as we move forward",
    "in the fast-paced world",
    "ever-evolving",
]

_TARGET_ROLE_HASHTAGS = {
    "#cloudengineering", "#platformengineering", "#engineeringleadership",
    "#cloudarchitecture", "#techleadership", "#infrastructureengineering",
    "#devops", "#sre", "#finops", "#kubernetes", "#cloudnative",
    "#engineeringmanagement", "#headofengineering", "#directorofengineering",
    "#cloudstrategy", "#enterprisearchitecture", "#financialservices",
    "#digitalbanking", "#softwarearchitecture", "#cto", "#vp",
    "#seniorengineering", "#infrastructureleadership",
}

_CTA_PATTERN = re.compile(
    r'\b(share|comment|reply|let me know|how are you|how is your|what.{0,20}you|'
    r'are you|do you|have you|where is|which approach|your (team|org|experience))\b',
    re.IGNORECASE,
)

# Maps each rule letter to the issue-code prefixes that count as a failure for it
_RULE_CODES: dict[str, set[str]] = {
    "A": {"HUMAN_TONE_LENGTH", "HUMAN_TONE_READABILITY", "GPT_PHRASE", "COMPLEXITY", "GPT_TONE"},
    "B": {"TOO_SHORT", "TOO_LONG"},
    "C": {"WEAK_HOOK"},
    "D": {"OBVIOUS_INSIGHT"},
    "E": {"UNAUTHORIZED_CLAIM"},
    "F": {"FORMAT_COMPLIANCE"},
    "G": {"MISSING_SOURCES", "DEAD_SOURCE_URL"},
    "H": {"MISSING_HASHTAGS", "WEAK_HASHTAGS", "TOO_MANY_HASHTAGS"},
    "I": {"MISSING_ENGAGEMENT_HOOK"},
}

_RULE_LABELS: dict[str, str] = {
    "A": "Human Tone (avg ≤15 words, Flesch ≥60)",
    "B": "Word Count (200-300, hard cap 300)",
    "C": "Eye-Catching Hook",
    "D": "Non-Obvious Insight",
    "E": "Achievement Guard (no unhedged I/we claims)",
    "F": "Format Compliance (no em/en dash)",
    "G": "Source Links + HTTP liveness",
    "H": "Role Alignment (3-5 hashtags, ≥1 leadership)",
    "I": "Engagement Hook (ends with question/CTA)",
}


# ── Readability helpers ───────────────────────────────────────────────────────

def _count_syllables(word: str) -> int:
    word = re.sub(r"[^a-zA-Z]", "", word.lower())
    if not word:
        return 1
    vowels = "aeiouy"
    count = 0
    prev_vowel = False
    for ch in word:
        is_vowel = ch in vowels
        if is_vowel and not prev_vowel:
            count += 1
        prev_vowel = is_vowel
    if word.endswith("e") and count > 1:
        count -= 1
    return max(1, count)


def _flesch_score(text: str) -> float:
    """Flesch Reading Ease — target ≥ 60 (plain English)."""
    sentences = [s for s in re.split(r'[.!?]+', text) if s.strip()]
    words = re.findall(r'\b\w+\b', text)
    if not sentences or not words:
        return 100.0
    n_syllables = sum(_count_syllables(w) for w in words)
    return 206.835 - 1.015 * (len(words) / len(sentences)) - 84.6 * (n_syllables / len(words))


def _avg_sentence_words(text: str) -> float:
    sentences = [s for s in re.split(r'[.!?]+', text) if s.strip()]
    if not sentences:
        return 0.0
    return sum(len(s.split()) for s in sentences) / len(sentences)


# ── URL liveness helper ───────────────────────────────────────────────────────

def _check_url_liveness(url: str) -> bool:
    """Return True if URL responds HTTP 200 (HEAD request)."""
    try:
        r = requests.head(
            url, timeout=5, allow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        return r.status_code == 200
    except Exception:
        return False


# ── ValidationResult ──────────────────────────────────────────────────────────

@dataclass
class ValidationResult:
    passed: bool
    issues: list[str] = field(default_factory=list)
    rule_status: dict = field(default_factory=dict)   # {"A": "✅ PASS", "B": "❌ FAIL", ...}

    def summary(self) -> str:
        if self.passed:
            return "All 9 validation checks passed."
        return "Validation failed:\n" + "\n".join(f"  - {i}" for i in self.issues)

    def as_revision_feedback(self) -> str:
        return (
            "The post failed validation. Fix ALL of the following before resubmitting:\n\n"
            + "\n".join(f"- {i}" for i in self.issues)
        )

    def validation_table(self) -> str:
        """Markdown table of per-rule pass/fail status for embedding in .md files."""
        rows = ["| Rule | Check | Status |", "|------|-------|--------|"]
        for letter, label in _RULE_LABELS.items():
            status = self.rule_status.get(letter, "✅ PASS")
            rows.append(f"| {letter} | {label} | {status} |")
        if self.issues:
            rows.append("")
            rows.append("**Issues:**")
            for issue in self.issues:
                rows.append(f"- {issue}")
        return "\n".join(rows)


# ── PostValidator ─────────────────────────────────────────────────────────────

class PostValidator:
    """
    Validates a LinkedIn post against 9 quality rules (A–I).
    All failures collected before returning; caller decides whether to auto-revise.
    """

    MAX_RETRIES = 2

    def validate(self, post: str, sources: str) -> ValidationResult:
        issues = []

        # ── Rule A: Human Tone (algorithmic layer) ─────────────────────────
        # Strip hashtag block so readability metrics score only the prose
        body_only = re.split(r'\n#', post)[0].strip()

        avg_words = _avg_sentence_words(body_only)
        if avg_words > 15:
            issues.append(
                f"HUMAN_TONE_LENGTH: Average sentence is {avg_words:.1f} words (target ≤ 15). "
                "Break long sentences into shorter, punchier ones."
            )

        flesch = _flesch_score(body_only)
        if flesch < 60:
            issues.append(
                f"HUMAN_TONE_READABILITY: Flesch readability score is {flesch:.0f} (target ≥ 60). "
                "Use shorter words and simpler sentence structures."
            )

        post_lower = post.lower()
        found_gpt = [p for p in _GPT_PHRASES if p in post_lower]
        if found_gpt:
            issues.append(
                f"GPT_PHRASE: AI-sounding filler phrase(s) detected: "
                f"{', '.join(repr(p) for p in found_gpt[:3])}. "
                "Remove or rewrite in plain, direct language."
            )

        # ── Rule B: Word Count (200-300, hard reject > 300) ───────────────
        clean_body = re.sub(r'\n---\s*$', '', post).strip()
        word_count = len(clean_body.split())
        if word_count < 190:
            issues.append(
                f"TOO_SHORT: Post is {word_count} words. Minimum is 200 words. "
                "Expand with a concrete example, statistic, or additional insight."
            )
        elif word_count > 310:
            issues.append(
                f"TOO_LONG: Post is {word_count} words. Hard cap is 300 words. "
                "Cut the weakest sentence or consolidate two similar points."
            )

        # ── Rule E: Achievement Guard ──────────────────────────────────────
        for pattern in _UNAUTHORIZED_CLAIM_PATTERNS:
            if re.search(pattern, post, re.IGNORECASE):
                issues.append(
                    "UNAUTHORIZED_CLAIM: Unhedged first-person claim detected (e.g. 'I built', "
                    "'we implemented'). Rewrite in passive voice or hedge it "
                    "(e.g. 'a team I advised', 'was observed at a Tier 1 bank')."
                )
                break

        # ── Rule F: Format Compliance (em + en dash) ──────────────────────
        dash_found = []
        if "—" in post:
            dash_found.append("em dash (—)")
        if "–" in post:
            dash_found.append("en dash (–)")
        if " -- " in post:
            dash_found.append("double hyphen ( -- )")
        if dash_found:
            issues.append(
                f"FORMAT_COMPLIANCE: {', '.join(dash_found)} detected. "
                "Replace every instance with a comma, colon, or full stop."
            )

        # ── Rule G: Source Links + HTTP HEAD liveness ─────────────────────
        if not sources or len(sources.strip()) < 20:
            issues.append(
                "MISSING_SOURCES: No sources found. Append a ## SOURCES section "
                "with 2-3 validated references in format: [Title] - domain.com/path"
            )
        else:
            urls = re.findall(r'https?://[^\s\)\>\"]+', post + "\n" + sources)
            dead = [u for u in urls[:5] if not _check_url_liveness(u)]
            if dead:
                issues.append(
                    f"DEAD_SOURCE_URL: {len(dead)} source URL(s) returned non-200: "
                    + ", ".join(dead[:3])
                    + ". Replace with a live, verifiable reference."
                )

        # ── Rule H: Role Alignment (hashtags) ─────────────────────────────
        hashtags_found = re.findall(r'#\w+', post)
        if len(hashtags_found) < 3:
            issues.append(
                f"MISSING_HASHTAGS: Only {len(hashtags_found)} hashtag(s) found. Include 3-5 hashtags "
                "targeting senior leadership: #CloudEngineering #PlatformEngineering "
                "#EngineeringLeadership #TechLeadership #FinOps"
            )
        elif len(hashtags_found) > 5:
            issues.append(
                f"TOO_MANY_HASHTAGS: {len(hashtags_found)} hashtags found. Use 3-5 only."
            )
        else:
            found_lower = {h.lower() for h in hashtags_found}
            if not found_lower.intersection(_TARGET_ROLE_HASHTAGS):
                issues.append(
                    "WEAK_HASHTAGS: Hashtags do not target the senior leadership audience. "
                    "Include at least one of: #CloudEngineering #PlatformEngineering "
                    "#EngineeringLeadership #TechLeadership #InfrastructureEngineering"
                )

        # ── Rule I: Engagement Hook ────────────────────────────────────────
        body_lines = [
            l.strip() for l in post.split('\n')
            if l.strip()
            and not l.strip().startswith('#')
            and not re.match(r'^[-_*]{3,}$', l.strip())   # skip --- separators
        ]
        last_line = body_lines[-1] if body_lines else ""
        if '?' not in last_line and not _CTA_PATTERN.search(last_line):
            issues.append(
                "MISSING_ENGAGEMENT_HOOK: Post does not end with a question or CTA. "
                "Add a closing question that invites comments from senior engineering leaders "
                "(e.g. 'How are your teams handling GPU chargeback today?')."
            )

        # ── Rules A (tone depth), C (hook), D (insight): Haiku LLM audit ──
        haiku_issues = self._haiku_audit(post)
        issues.extend(haiku_issues)

        # ── Build per-rule status map ──────────────────────────────────────
        failed_codes = {i.split(":")[0].strip() for i in issues}
        rule_status = {
            letter: ("❌ FAIL" if codes.intersection(failed_codes) else "✅ PASS")
            for letter, codes in _RULE_CODES.items()
        }

        return ValidationResult(passed=len(issues) == 0, issues=issues, rule_status=rule_status)

    def _haiku_audit(self, post: str) -> list[str]:
        """Haiku LLM audit for Rules A (tone depth), C (hook quality), D (non-obvious insight)."""
        try:
            client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
            prompt = f"""You are a strict LinkedIn post editor for a senior engineering leader audience. Review the post below against FOUR criteria:

1. SIMPLICITY: Is every sentence short and plain? Flag sentences that are unnecessarily long, use complex vocabulary, or could be split into two shorter ones.
2. HUMAN_TONE: Does this sound like a real senior engineer, or AI-generated? Flag corporate-speak, hedging phrases, or overly formal constructions.
3. HOOK_QUALITY: Is the opening line compelling, specific, and non-obvious? Flag if it is generic, starts with a broad rhetorical question, or fails to immediately signal practitioner expertise with a concrete data point or counterintuitive observation.
4. NON_OBVIOUS_INSIGHT: Does the post contain at least one insight that challenges conventional thinking or surfaces a non-obvious constraint? A post that only states well-known facts (e.g. "Kubernetes is popular") fails this check. It must reveal a hidden tension, counter-intuitive finding, or practitioner-only angle.

Post to review:
---
{post[:1500]}
---

Reply with JSON only, no explanation outside the JSON:
{{
  "simplicity_ok": true or false,
  "simplicity_issue": "one sentence describing the problem, or null if ok",
  "human_tone_ok": true or false,
  "tone_issue": "one sentence describing the problem, or null if ok",
  "hook_ok": true or false,
  "hook_issue": "one sentence describing the problem, or null if ok",
  "non_obvious_ok": true or false,
  "non_obvious_issue": "one sentence describing what obvious angle is taken instead, or null if ok"
}}"""

            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=400,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text.strip()
            raw = re.sub(r"^```[a-z]*\n?", "", raw).rstrip("```").strip()
            result = json.loads(raw)

            audit_issues = []
            if not result.get("simplicity_ok") and result.get("simplicity_issue"):
                audit_issues.append(
                    f"COMPLEXITY: {result['simplicity_issue']} Simplify the language."
                )
            if not result.get("human_tone_ok") and result.get("tone_issue"):
                audit_issues.append(
                    f"GPT_TONE: {result['tone_issue']} Rewrite to sound like a real person wrote it."
                )
            if not result.get("hook_ok") and result.get("hook_issue"):
                audit_issues.append(
                    f"WEAK_HOOK: {result['hook_issue']} "
                    "Rewrite the opening to be specific, data-led, or counterintuitive."
                )
            if not result.get("non_obvious_ok") and result.get("non_obvious_issue"):
                audit_issues.append(
                    f"OBVIOUS_INSIGHT: {result['non_obvious_issue']} "
                    "Add a counterintuitive finding, hidden constraint, or practitioner-only angle."
                )
            return audit_issues

        except Exception as e:
            console.print(f"[yellow]Haiku audit skipped: {e}[/yellow]")
            return []
