"""Plain-language pass for the executive sections of a Deep Diagnostic.

Customers said the reports open with too much prose and too much marketing / SEO jargon.
After the analysis (and after any assessment synthesis), this rewrites three fields on the
result to fixed caps, in words a hospital executive uses:

  • market_overview        → one paragraph, ≤ 80 words (who / where / what kind / bottom line)
  • ai_visibility_verdict  → ≤ 3 sentences: score, the reason, the one thing to do
  • top_recommendation     → 3–5 "what to do first" bullets (one action + one reason each)

"What AI assistants currently see" is deliberately untouched — customers value it as written.
Numbers, names and facts come only from the original text; nothing is invented. Fail-soft:
on any error the originals are trimmed deterministically instead.
"""
from __future__ import annotations

import json
import re

_MODEL = "claude-haiku-4-5-20251001"

OVERVIEW_WORDS = 80
VERDICT_SENTENCES = 3
BULLETS_MIN, BULLETS_MAX = 3, 5

# Words the executive sections must not use → what to say instead.
PLAIN_TERMS = [
    ("retrieval-time / training-data signals", "what assistants look up live vs. what they already know"),
    ("training-data anchor / durable authoritative anchor", "a reliable public record (for example a Wikipedia entry)"),
    ("entity resolution / entity-resolution integrity", "whether assistants can tell your locations apart"),
    ("linkage integrity / physician–practice linkage", "whether assistants connect your doctors to your organization"),
    ("NAP consistency", "the same name, address and phone everywhere"),
    ("canonical domain", "one official website address"),
    ("Schema.org / structured data / JSON-LD / llms.txt / crawlable HTML", "machine-readable pages (leave this to the technical roadmap)"),
    ("weighting profile (procedural / relationship)", "scored as a surgical hospital / scored as a primary-care organization"),
    ("footprint fragmentation", "clinic listings that contradict each other"),
    ("front-door rating", "main Google rating"),
    ("third-party aggregate", "ratings on other review sites"),
    ("adversarial prompts / query battery", "the questions patients ask assistants"),
    ("SEO / GEO / AEO", "being found by search and AI assistants"),
]

_SYSTEM = (
    "You edit healthcare reputation reports for hospital and practice executives who are not "
    "marketing or web specialists. Rewrite the three sections you are given so a CEO can read them "
    "in under a minute. Rules:\n"
    "- Plain English. Short sentences. No parentheses, no semicolons, no section numbers, no "
    "quotation marks around ordinary words.\n"
    "- Use ONLY facts, names and numbers that appear in the material you are given. Never add "
    "facts, never change a number, never soften or sharpen a finding.\n"
    "- Never use these terms (say the plain version instead): "
    + "; ".join(f"'{a}' → '{b}'" for a, b in PLAIN_TERMS) + ".\n"
    "- Technical instructions (schema markup, llms.txt, sitemaps) belong in the roadmap that "
    "follows, not in these sections — refer to them only as 'technical fixes for your web team'.\n"
    f"- overview: ONE paragraph, at most {OVERVIEW_WORDS} words: who the organization is, where, "
    "what kind of organization, and one sentence on the bottom line.\n"
    f"- verdict: at most {VERDICT_SENTENCES} sentences: the score out of 100 and its national "
    "standing if given, the main reason, and the one thing that would move it most.\n"
    f"- first_moves: {BULLETS_MIN} to {BULLETS_MAX} bullets, each ONE action and ONE reason in "
    "at most 25 words, ordered by impact, drawn from the assessment and roadmap given.\n"
    "Return JSON only: {\"overview\": str, \"verdict\": str, \"first_moves\": [str, ...]}."
)


def _client():
    import anthropic
    return anthropic.Anthropic()


def _sentences(text: str, n: int) -> str:
    parts = re.split(r"(?<=[.!?])\s+", (text or "").strip())
    return " ".join(p for p in parts[:n] if p).strip()


def _words(text: str, n: int) -> str:
    w = (text or "").split()
    if len(w) <= n:
        return (text or "").strip()
    cut = " ".join(w[:n])
    m = re.search(r"^(.*[.!?])\s", cut + " ")
    return (m.group(1) if m else cut.rstrip(",;:") + ".").strip()


def bullets_to_text(items: list[str]) -> str:
    return "\n".join("• " + re.sub(r"^[\s•\-\*\d.)]+", "", i).strip() for i in items if i and i.strip())


def is_bullets(text: str | None) -> bool:
    return bool(text) and text.lstrip().startswith("•")


def _fallback(result) -> None:
    result.market_overview = _words(_sentences(result.market_overview, 3), OVERVIEW_WORDS)
    result.ai_visibility_verdict = _sentences(result.ai_visibility_verdict, VERDICT_SENTENCES)
    # Assessment: keep the first three sentences as a single bullet so the layout is consistent.
    if result.top_recommendation and not is_bullets(result.top_recommendation):
        result.top_recommendation = bullets_to_text([_sentences(result.top_recommendation, 3)])


def condense(result, *, only_assessment: bool = False, console=None) -> bool:
    """Rewrite the executive sections in place. Returns True when anything was changed
    (model pass or deterministic fallback), False when there was nothing to do.

    only_assessment=True re-runs just the bullets (used after the practice assessment is
    re-synthesized with content findings, which replaces top_recommendation with a paragraph)."""
    if not getattr(result, "individual_report", False):
        return False
    if getattr(result, "plain_language", False) and not only_assessment:
        return False
    if only_assessment and is_bullets(result.top_recommendation):
        return False
    p = next(iter(result.rankings or []), None)
    score = getattr(p, "ai_visibility_score", None) if p else None
    roadmap = []
    for sec in (getattr(result, "improvement_sections", None) or [])[:4]:
        for it in (getattr(sec, "items", None) or [])[:3]:
            roadmap.append(f"- {it}")
    material = {
        "organization": result.entity_name or result.location,
        "type": result.entity_type or "hospital",
        "score_out_of_100": score,
        "national_quartile": getattr(result, "national_quartile", None) or getattr(p, "national_quartile", None) if p else None,
        "overview": result.market_overview or "",
        "verdict": result.ai_visibility_verdict or "",
        "assessment": result.top_recommendation or "",
        "roadmap_items": roadmap[:12],
    }
    try:
        resp = _client().messages.create(
            model=_MODEL, max_tokens=900, temperature=0, system=_SYSTEM,
            messages=[{"role": "user", "content": "Material:\n" + json.dumps(material, ensure_ascii=False, indent=1)
                       + ("\n\nRewrite only first_moves (still return all three keys; copy overview and verdict through unchanged)." if only_assessment else "\n\nRewrite all three.")}],
        )
        txt = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
        m = re.search(r"\{.*\}", txt, re.S)
        data = json.loads(m.group(0) if m else txt)
        moves = [str(x).strip() for x in (data.get("first_moves") or []) if str(x).strip()]
        if not (BULLETS_MIN <= len(moves) <= BULLETS_MAX + 1):
            raise ValueError("bullet count out of range")
        if not only_assessment:
            ov = str(data.get("overview") or "").strip()
            vd = str(data.get("verdict") or "").strip()
            if not ov or not vd:
                raise ValueError("empty rewrite")
            result.market_overview = _words(ov, OVERVIEW_WORDS + 15)
            result.ai_visibility_verdict = _sentences(vd, VERDICT_SENTENCES)
        result.top_recommendation = bullets_to_text(moves[:BULLETS_MAX])
        result.plain_language = True
        if console:
            console.print("[green]✓[/green] Executive sections condensed to plain language")
        return True
    except Exception as exc:
        if console:
            console.print(f"[yellow]⚠[/yellow] Plain-language pass failed ({type(exc).__name__}: {exc}); trimming instead.")
        if not only_assessment:
            _fallback(result)
            result.plain_language = True
            return True
        if result.top_recommendation:
            result.top_recommendation = bullets_to_text([_sentences(result.top_recommendation, 3)])
            return True
        return False
