"""Observed spot-check: ask a few patient-style questions of real assistants and record
whether this organization was named, where it ranked, who was named instead, and which
pages were cited. Observational only — it never changes the Pulse Score.

Runners: Claude (Haiku 4.5 + web search; always), OpenAI (Responses API + web_search when
OPENAI_API_KEY is set), Gemini (Google Search grounding when GEMINI_API_KEY is set).
Each answer is parsed once by Haiku into an ordered list of organizations named.
"""
from __future__ import annotations

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Optional
from urllib.parse import urlparse

from . import cost_tracker as _cost

_PARSE_MODEL = "claude-haiku-4-5-20251001"
_CLAUDE_MODEL = "claude-haiku-4-5-20251001"
_OPENAI_MODEL = os.environ.get("SPOTCHECK_OPENAI_MODEL", "gpt-5-mini")
_GEMINI_MODEL = os.environ.get("SPOTCHECK_GEMINI_MODEL", "gemini-3.6-flash")   # 2.5-flash is retired for new API users
_MAX_QUERIES = 8

_CONDITION = {  # specialty keyword → a patient-language condition/procedure
    "ortho": "a knee replacement", "spine": "back surgery", "cardio": "a heart valve problem",
    "cardiac": "heart surgery", "derm": "a skin cancer screening", "primary": "a new primary care doctor",
    "family": "a new family doctor", "internal": "a new primary care doctor", "pediatr": "a pediatrician for my child",
    "urolog": "kidney stones", "oncolog": "cancer treatment", "cancer": "cancer treatment",
    "behavioral": "a therapist", "psych": "a psychiatrist", "mental": "a therapist",
    "neuro": "migraines", "gastro": "a colonoscopy", "ent": "sinus surgery", "ophthalm": "cataract surgery",
    "obgyn": "an OB-GYN", "obstet": "an OB-GYN", "gynec": "an OB-GYN", "endocrin": "diabetes care",
    "rheumat": "rheumatoid arthritis", "pulmon": "asthma care", "podiat": "a foot surgeon",
    "plastic": "a plastic surgeon", "vascular": "a vascular surgeon", "sports": "a sports injury",
    "physical therapy": "physical therapy", "urgent": "urgent care",
}
_ES = {"ortho": "ortopedista", "cardio": "cardiólogo", "derm": "dermatólogo", "pediatr": "pediatra",
       "primary": "médico de cabecera", "family": "médico de familia", "urolog": "urólogo", "oncolog": "oncólogo",
       "cancer": "oncólogo", "behavioral": "terapeuta", "psych": "psiquiatra", "obgyn": "ginecólogo", "neuro": "neurólogo"}


def _condition(specialty: str) -> str:
    s = (specialty or "").lower()
    for k, v in _CONDITION.items():
        if k in s:
            return v
    return f"{specialty.lower()} care" if specialty else "a hospital"


def _spec_es(specialty: str) -> str:
    s = (specialty or "").lower()
    for k, v in _ES.items():
        if k in s:
            return v
    return "hospital"


def build_queries(entity_name: str, city: str, state: str, specialty: Optional[str],
                  entity_type: str = "hospital") -> list[dict]:
    """Eight fixed patient-style questions for this entity's type and market."""
    loc = f"{city}, {state}"
    is_practice = entity_type in ("practice", "service_line") and bool(specialty)
    spec = (specialty or "").strip()
    spec_l = spec.lower() if spec else ""
    if is_practice:
        q = [
            ("best_in_market", f"Who are the best {spec_l} doctors in {loc}?"),
            ("condition", f"I need {_condition(spec)} in {loc}. Which practice should I go to?"),
            ("near_me", f"Top-rated {spec_l} practices near {city} {state}"),
            ("insurance", f"Which {spec_l} practices in {loc} accept Medicaid or Medicare?"),
            ("urgency", f"I need a same-day or this-week {spec_l} appointment in {loc}. Where should I try?"),
            ("direct", f"Is {entity_name} in {loc} a good choice for {spec_l}? What do patients say?"),
            ("compare", f"Compare the {spec_l} groups in {loc} — who has the best patient reviews?"),
            ("spanish", f"¿Cuál es el mejor {_spec_es(spec)} en {loc}?"),
        ]
    else:
        q = [
            ("best_in_market", f"What is the best hospital in {loc}?"),
            ("condition", f"I need {_condition(spec) if spec else 'surgery'} in {loc}. Which hospital should I choose?"),
            ("near_me", f"Top-rated hospitals near {city} {state} and why"),
            ("insurance", f"Which hospitals in {loc} accept Medicare and Medicaid and have good ratings?"),
            ("urgency", f"Best emergency room in {loc} right now?"),
            ("direct", f"Is {entity_name} in {loc} a good hospital? What are patients and ratings saying?"),
            ("compare", f"Compare the hospitals in {loc} on quality and patient satisfaction."),
            ("spanish", f"¿Cuál es el mejor hospital en {loc}?"),
        ]
    return [{"key": k, "query": t} for k, t in q[:_MAX_QUERIES]]


# ── Runners ──────────────────────────────────────────────────────────────────
_SYS = ("You are a helpful assistant answering a patient. Answer the way you normally would for a "
        "consumer, naming specific organizations when you recommend any. Keep it under 250 words.")


def run_claude(query: str) -> dict:
    import anthropic
    client = anthropic.Anthropic()
    tool = {"type": "web_search_20250305", "name": "web_search", "max_uses": 3}
    text, cites = "", []
    resp = client.messages.create(model=_CLAUDE_MODEL, max_tokens=700, system=_SYS, tools=[tool],
                                  messages=[{"role": "user", "content": query}])
    for b in resp.content:
        if getattr(b, "type", "") == "text":
            text += b.text
            for c in (getattr(b, "citations", None) or []):
                u = getattr(c, "url", None)
                if u:
                    cites.append(u)
        elif getattr(b, "type", "") == "web_search_tool_result":
            for r in (getattr(b, "content", None) or []):
                u = getattr(r, "url", None)
                if u:
                    cites.append(u)
    return {"assistant": "Claude", "text": text, "citations": _dedupe(cites)}


def run_openai(query: str) -> Optional[dict]:
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        return None
    from openai import OpenAI
    client = OpenAI(api_key=key)
    # max_tool_calls bounds spend: one patient question needs a handful of searches, not seven.
    resp = client.responses.create(model=_OPENAI_MODEL, tools=[{"type": "web_search"}],
                                   include=["web_search_call.action.sources"],
                                   max_tool_calls=int(os.environ.get("SPOTCHECK_OPENAI_MAX_SEARCHES", "3")),
                                   instructions=_SYS, input=query)
    text, cites, n_searches = "", [], 0
    for item in getattr(resp, "output", []) or []:
        t = getattr(item, "type", "")
        if t == "message":
            for part in getattr(item, "content", []) or []:
                if getattr(part, "type", "") == "output_text":
                    text += getattr(part, "text", "") or ""
                    for a in getattr(part, "annotations", None) or []:
                        u = getattr(a, "url", None)
                        if u:
                            cites.append(u)
        elif t == "web_search_call":
            n_searches += 1
            act = getattr(item, "action", None)
            for src in (getattr(act, "sources", None) or []):
                u = getattr(src, "url", None) or (src.get("url") if isinstance(src, dict) else None)
                if u:
                    cites.append(u)
    try:
        u = resp.usage
        _cost.record_openai(_OPENAI_MODEL, int(getattr(u, "input_tokens", 0) or 0), int(getattr(u, "output_tokens", 0) or 0), web_searches=max(1, n_searches))
    except Exception:
        pass
    return {"assistant": "ChatGPT", "text": text, "citations": _dedupe(cites)}


def run_gemini(query: str) -> Optional[dict]:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        return None
    import httpx
    payload = {"system_instruction": {"parts": [{"text": _SYS}]},
               "contents": [{"parts": [{"text": query}]}],
               "tools": [{"google_search": {}}],
               "generationConfig": {"maxOutputTokens": 700}}
    resp = httpx.post(f"https://generativelanguage.googleapis.com/v1beta/models/{_GEMINI_MODEL}:generateContent?key={key}",
                      json=payload, timeout=90)
    resp.raise_for_status()
    data = resp.json()
    cand = (data.get("candidates") or [{}])[0]
    text = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []))
    cites = []
    gm = cand.get("groundingMetadata") or {}
    for ch in gm.get("groundingChunks") or []:
        u = (ch.get("web") or {}).get("uri")
        if u:
            cites.append(u)
    _cost.record_gemini_grounding(_GEMINI_MODEL)
    return {"assistant": "Gemini", "text": text, "citations": _dedupe(cites)}


def _dedupe(urls: list) -> list:
    seen, out = set(), []
    for u in urls:
        if u and u not in seen:
            seen.add(u); out.append(u)
    return out


# ── Parsing ──────────────────────────────────────────────────────────────────
def _norm(s: str) -> str:
    s = re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower())
    s = re.sub(r"\b(the|of|at|and|inc|llc|llp|pc|pa|md|dr|clinic|center|centers|medical|health|hospital|group|associates|orthopaedics|orthopedics)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _is_us(name: str, aliases: list) -> bool:
    n = _norm(name)
    if not n:
        return False
    for a in aliases:
        an = _norm(a)
        if not an:
            continue
        if n == an or n in an or an in n:
            return True
        toks, atoks = set(n.split()), set(an.split())
        if toks and len(toks & atoks) / len(toks | atoks) >= 0.6:
            return True
    return False


def parse_answer(text: str) -> list[str]:
    """Ordered list of healthcare organizations named in an answer (Haiku, JSON)."""
    if not (text or "").strip():
        return []
    import anthropic
    client = anthropic.Anthropic()
    prompt = ("List the healthcare organizations (hospitals, health systems, practices, clinics) that this answer "
              "names or recommends, in the order they appear. Return ONLY a JSON array of strings with the names "
              "exactly as written. Skip generic mentions and physician names without an organization.\n\nANSWER:\n" + text[:6000])
    resp = client.messages.create(model=_PARSE_MODEL, max_tokens=400, messages=[{"role": "user", "content": prompt}])
    raw = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    m = re.search(r"\[.*\]", raw, re.S)
    try:
        arr = json.loads(m.group(0)) if m else []
        return [str(x).strip() for x in arr if str(x).strip()][:15]
    except Exception:
        return []


# ── Orchestration ────────────────────────────────────────────────────────────
def run_spotcheck(entity_name: str, city: str, state: str, specialty: Optional[str], entity_type: str,
                  aliases: Optional[list] = None, website: Optional[str] = None, emit=None) -> dict:
    """Run the 8 queries against every configured assistant; return the observed panel."""
    queries = build_queries(entity_name, city, state, specialty, entity_type)
    aliases = [entity_name] + [a for a in (aliases or []) if a]
    runners = [("Claude", run_claude)]
    if os.environ.get("OPENAI_API_KEY"):
        runners.append(("ChatGPT", run_openai))
    if os.environ.get("GEMINI_API_KEY"):
        runners.append(("Gemini", run_gemini))
    if emit:
        emit({"type": "text", "text": f"\nObserved check: asking {len(queries)} patient questions of {', '.join(n for n, _ in runners)}…"})

    def _one(args):
        name, fn, q = args
        try:
            r = fn(q["query"])
            if not r:
                return None
            named = parse_answer(r["text"])
            us = [i for i, n in enumerate(named) if _is_us(n, aliases)]
            return {"assistant": name, "key": q["key"], "query": q["query"], "named": named,
                    "mentioned": bool(us), "rank": (us[0] + 1) if us else None,
                    "competitors": [n for i, n in enumerate(named) if i not in us][:6],
                    "citations": r["citations"][:12], "answer": (r["text"] or "")[:1200]}
        except Exception as exc:
            return {"assistant": name, "key": q["key"], "query": q["query"], "error": f"{type(exc).__name__}: {str(exc)[:120]}"}

    jobs = [(n, fn, q) for n, fn in runners for q in queries]
    with ThreadPoolExecutor(max_workers=6) as ex:
        results = [r for r in ex.map(_one, jobs) if r]

    our_domain = None
    if website:
        try:
            our_domain = urlparse(website if "://" in website else "https://" + website).netloc.lower().replace("www.", "")
        except Exception:
            our_domain = None
    per_assistant = []
    for name, _ in runners:
        rs = [r for r in results if r["assistant"] == name and "error" not in r]
        errs = [r for r in results if r["assistant"] == name and "error" in r]
        ment = [r for r in rs if r["mentioned"]]
        ranks = [r["rank"] for r in ment if r.get("rank")]
        per_assistant.append({"assistant": name, "asked": len(rs), "errors": len(errs), "mentioned": len(ment),
                              "avg_rank": round(sum(ranks) / len(ranks), 1) if ranks else None,
                              "direct_ok": any(r["key"] == "direct" and r["mentioned"] for r in rs)})
    comp: dict = {}
    for r in results:
        for c in r.get("competitors") or []:
            comp[c] = comp.get(c, 0) + 1
    top_comp = sorted(comp.items(), key=lambda kv: -kv[1])[:6]
    domains: dict = {}
    for r in results:
        for u in r.get("citations") or []:
            try:
                d = urlparse(u).netloc.lower().replace("www.", "")
            except Exception:
                continue
            if d:
                domains[d] = domains.get(d, 0) + 1
    top_domains = sorted(domains.items(), key=lambda kv: -kv[1])[:10]
    ok = [r for r in results if "error" not in r]
    non_direct = [r for r in ok if r["key"] != "direct"]
    from .citations import analyze as _cite_analyze
    return _cite_analyze({
        "date": date.today().isoformat(), "queries": len(queries), "assistants": [n for n, _ in runners],
        "asked": len(ok), "mentioned": sum(1 for r in ok if r["mentioned"]),
        "unprompted_asked": len(non_direct), "unprompted_mentioned": sum(1 for r in non_direct if r["mentioned"]),
        "per_assistant": per_assistant, "top_competitors": [{"name": n, "count": c} for n, c in top_comp],
        "cited_domains": [{"domain": d, "count": c, "ours": bool(our_domain and (d == our_domain or d.endswith("." + our_domain)))} for d, c in top_domains],
        "our_domain": our_domain, "our_domain_cited": any(d == our_domain or (our_domain and d.endswith("." + our_domain)) for d, _ in top_domains) if our_domain else None,
        "results": results,
    })


def summary_sentence(sc: dict) -> str:
    if not sc or not sc.get("asked"):
        return ""
    n, m = sc["unprompted_asked"], sc["unprompted_mentioned"]
    parts = [f"Named in {m} of {n} unprompted patient questions" if n else ""]
    tc = sc.get("top_competitors") or []
    if tc:
        parts.append("named instead: " + ", ".join(x["name"] for x in tc[:3]))
    if sc.get("our_domain") is not None:
        parts.append("your website was " + ("cited" if sc.get("our_domain_cited") else "not cited"))
    return "; ".join(p for p in parts if p) + "."
