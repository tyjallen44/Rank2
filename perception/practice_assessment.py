"""Diagnostic Assessment synthesis for the practice combined report.

A small, dedicated LLM step (option b) that rewrites the practice's AI Visibility
Assessment so it explicitly cites the verified content findings — tying the
four-pillar read together with the specific, checkable gaps the Content Report
then prescribes fixes for. Never fabricates: it works only from the four-pillar
narrative and the findings it is given.
"""
from __future__ import annotations

import anthropic

client = anthropic.Anthropic()
_MODEL = "claude-opus-4-8"

from .plain import PLAIN_TERMS, BULLETS_MAX, bullets_to_text, is_bullets

_SYSTEM = (
    "You write the 'What to do first' list for a practice's AI reputation report, read by the "
    "practice's leadership (not marketing or web specialists). From the four-pillar read and the "
    "VERIFIED content findings you are given, write 3 to 5 bullets, ordered by impact. Each bullet is "
    "ONE action and ONE reason, at most 25 words, and where it rests on a finding it names that finding "
    "in plain words so the list and the fixes that follow read as one story.\n"
    "Plain English, short sentences, no parentheses, no jargon. Never use these terms (say the plain "
    "version instead): " + "; ".join(f"'{a}' → '{b}'" for a, b in PLAIN_TERMS) + ".\n"
    "Use ONLY the material provided — never invent facts, numbers or findings. Return the bullets only, "
    "one per line, each starting with '• '. No headers, no preamble."
)


def synthesize_assessment(entity_name: str, location: str, base_assessment: str,
                          verdict: str, findings: list) -> str:
    """Return a Diagnostic Assessment paragraph that cites the findings. Falls back
    to the base four-pillar assessment on any error (never blocks the report)."""
    try:
        lines = []
        for f in (findings or [])[:12]:
            sev = getattr(f, "severity", None) or (f.get("severity") if isinstance(f, dict) else "")
            summ = getattr(f, "teaser_summary", None) or (f.get("teaser_summary") if isinstance(f, dict) else "")
            if summ:
                lines.append(f"- [{sev}] {summ}")
        if not lines:
            return base_assessment or ""
        prompt = (
            f"Practice: {entity_name} ({location}).\n\n"
            f"Four-pillar AI-visibility read (context, do not just repeat verbatim):\n{verdict or base_assessment}\n\n"
            f"Verified content findings (cite these specifically):\n" + "\n".join(lines) +
            "\n\nWrite the 'What to do first' bullets."
        )
        resp = client.messages.create(
            model=_MODEL, max_tokens=600, system=_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        out = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
        if not out:
            return base_assessment or ""
        items = [ln for ln in out.splitlines() if ln.strip()]
        text = bullets_to_text(items[:BULLETS_MAX])
        return text if is_bullets(text) else (base_assessment or "")
    except Exception:
        return base_assessment or ""
