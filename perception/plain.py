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


def _log(msg: str, console=None) -> None:
    line = f"[plain] {msg}"
    if console:
        console.print(line)
    else:
        print(line, flush=True)


def _sentence_bullets(text: str, n: int = 4) -> str:
    """Fallback bullets: the first n sentences, one per bullet (readable even if not rewritten)."""
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", (text or "").strip()) if p.strip()]
    return bullets_to_text(parts[:n])


def looks_fallback(result) -> bool:
    """True when the executive sections still look like trimmed originals (a bullet over 45 words,
    only one bullet, or a verdict over 90 words) — i.e. the model pass never succeeded on them."""
    tr = getattr(result, "top_recommendation", "") or ""
    items = [ln for ln in tr.splitlines() if ln.strip()]
    if not is_bullets(tr) or len(items) < 2 or any(len(i.split()) > 45 for i in items):
        return True
    return len((getattr(result, "ai_visibility_verdict", "") or "").split()) > 90


def _fallback(result) -> None:
    result.market_overview = _words(_sentences(result.market_overview, 3), OVERVIEW_WORDS)
    result.ai_visibility_verdict = _sentences(result.ai_visibility_verdict, VERDICT_SENTENCES)
    if result.top_recommendation and not is_bullets(result.top_recommendation):
        result.top_recommendation = _sentence_bullets(result.top_recommendation)


_STRUCT_SYSTEM = (
    "You edit healthcare reputation reports for hospital executives. Turn the section you are given "
    "into a one-sentence headline (at most 25 words) that states the main point, followed by 3 to 5 "
    "bullets (each at most 22 words, one idea each) that support it. Keep every fact, name and number "
    "from the source; add nothing. Plain English, no parentheses, no jargon (say 'what assistants look "
    "up live' not 'retrieval-time', 'whether assistants can tell your locations apart' not 'entity "
    "resolution'). Return JSON only: {\"headline\": str, \"bullets\": [str, ...]}."
)


def structure_prose(text: str, *, label: str = "section", console=None) -> Optional[dict]:
    """{'headline', 'bullets'} for a prose section, via the model; deterministic sentence split on failure."""
    t = (text or "").strip()
    if not t:
        return None
    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", t) if s.strip()]
    fallback = {"headline": sents[0] if sents else t[:200], "bullets": sents[1:6]}
    try:
        resp = _client().messages.create(
            model=_MODEL, max_tokens=700, system=_STRUCT_SYSTEM,
            messages=[{"role": "user", "content": f"Section ({label}):\n{t[:6000]}\n\nReturn the JSON."}],
        )
        txt = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
        m = re.search(r"\{.*\}", txt, re.S)
        data = json.loads(m.group(0) if m else txt)
        head = str(data.get("headline") or "").strip()
        bullets = [str(x).strip() for x in (data.get("bullets") or []) if str(x).strip()]
        if not head or len(bullets) < 2:
            raise ValueError("thin structure")
        return {"headline": head, "bullets": bullets[:5]}
    except Exception as exc:
        _log(f"structure_prose failed for {label} ({type(exc).__name__}: {str(exc)[:120]}); sentence split used.", console)
        return fallback if fallback["bullets"] else {"headline": fallback["headline"], "bullets": []}


def condense_network(result, console=None) -> bool:
    """Hospital Network: executive summary and 'What AI assistants currently see' → headline + bullets."""
    changed = False
    if getattr(result, "executive_summary", "") and not getattr(result, "executive_summary_structured", None):
        result.executive_summary_structured = structure_prose(result.executive_summary, label="executive summary", console=console)
        changed = True
    if getattr(result, "ai_says", "") and not getattr(result, "ai_says_structured", None):
        result.ai_says_structured = structure_prose(result.ai_says, label="what AI assistants currently see", console=console)
        changed = True
    return changed


def condense(result, *, only_assessment: bool = False, console=None) -> bool:
    """Rewrite the executive sections in place. Returns True when anything was changed
    (model pass or deterministic fallback), False when there was nothing to do.

    only_assessment=True re-runs just the bullets (used after the practice assessment is
    re-synthesized with content findings, which replaces top_recommendation with a paragraph)."""
    if not getattr(result, "individual_report", False):
        return False
    if getattr(result, "plain_language", False) and not only_assessment and not looks_fallback(result):
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
    if only_assessment:
        material.pop("overview", None); material.pop("verdict", None)
    ask = ("Rewrite ONLY first_moves from the assessment and roadmap. Return JSON {\"first_moves\": [...]} and nothing else."
           if only_assessment else "Rewrite all three.")
    last_err = None
    for attempt in (1, 2):
        try:
            resp = _client().messages.create(
                model=_MODEL, max_tokens=1500, system=_SYSTEM,
                messages=[{"role": "user", "content": "Material:\n" + json.dumps(material, ensure_ascii=False, indent=1) + "\n\n" + ask}],
            )
            txt = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
            m = re.search(r"\{.*\}", txt, re.S)
            data = json.loads(m.group(0) if m else txt)
            moves = [str(x).strip() for x in (data.get("first_moves") or []) if str(x).strip()]
            if len(moves) < 2:
                raise ValueError(f"only {len(moves)} bullets returned")
            last_err = None
            break
        except Exception as exc:
            last_err = exc
            _log(f"attempt {attempt} failed run={getattr(result, 'run_id', '?')}: {type(exc).__name__}: {str(exc)[:160]}", console)
    try:
        if last_err is not None:
            raise last_err
        if not only_assessment:
            ov = str(data.get("overview") or "").strip()
            vd = str(data.get("verdict") or "").strip()
            if not ov or not vd:
                raise ValueError("empty rewrite")
            result.market_overview = _words(ov, OVERVIEW_WORDS + 15)
            result.ai_visibility_verdict = _sentences(vd, VERDICT_SENTENCES)
        result.top_recommendation = bullets_to_text(moves[:BULLETS_MAX])
        result.plain_language = True
        _log(f"condensed run={getattr(result, 'run_id', '?')} ({'assessment only' if only_assessment else 'all sections'})", console)
        return True
    except Exception as exc:
        _log(f"model pass failed run={getattr(result, 'run_id', '?')} ({type(exc).__name__}: {str(exc)[:160]}); trimming instead.", console)
        if not only_assessment:
            _fallback(result)           # readable now; plain_language stays False so the next request retries
            return True
        if result.top_recommendation:
            result.top_recommendation = _sentence_bullets(result.top_recommendation)
            return True
        return False
