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

_SYSTEM = (
    "You are a healthcare AI-visibility analyst writing the 'Diagnostic Assessment' paragraph "
    "for a practice report. Interpret how AI assistants currently see this practice and name the "
    "single biggest lever, then explicitly reference the specific VERIFIED content findings you are "
    "given (by their plain description) so the reader sees the assessment and the fixes that follow "
    "are one coherent story. 3–5 sentences, plain and factual. Use ONLY the material provided — never "
    "invent facts, numbers, or findings. Do not use markdown or headers; return the paragraph text only."
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
            "\n\nWrite the Diagnostic Assessment paragraph."
        )
        resp = client.messages.create(
            model=_MODEL, max_tokens=500, system=_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        out = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
        return out or (base_assessment or "")
    except Exception:
        return base_assessment or ""
