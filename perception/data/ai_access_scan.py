"""AI Access Scan — admin-only batch check of whether a list of organizations' websites let AI
assistants read them.

One site at a time this is the same test the reports run on page 1 (`probe_ai_crawlers`: request
the homepage as GPTBot, ChatGPT-User, ClaudeBot, PerplexityBot, Google-Extended and a plain
browser) plus a read of robots.txt. Here it is run across a whole event attendee list so the
share of prospects whose sites are hidden from AI can be measured.

Classification per site (decided by the named crawlers GPTBot, ChatGPT-User, ClaudeBot and
PerplexityBot; Google-Extended is a robots.txt token with no crawler UA and is recorded only):
  no_website   — no address to test
  blocked      — some crawlers get in and others are turned away (a selective bot rule), or every
                 crawler is turned away while a browser request gets in → red in the reports
  robots       — crawlers get in but robots.txt tells one or more AI crawlers to stay out → amber
  open         — every named crawler gets in (even if our browser-like request was challenged —
                 WAFs fingerprint a scripted client; what matters is that the AI readers pass)
  unreachable  — every request, browser included, is turned away or fails (site down, IP
                 reputation, rate limit); nothing can be said, NOT counted as blocked
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Optional

from perception.data.website_facts import probe_ai_crawlers

BUCKETS = ["blocked", "robots", "open", "unreachable", "no_website"]
BUCKET_LABEL = {
    "blocked": "Blocks AI crawlers (firewall / bot wall)",
    "robots": "robots.txt tells AI crawlers to stay out",
    "open": "Open to AI crawlers",
    "unreachable": "Unreachable from our servers",
    "no_website": "No website to test",
}

_ROBOTS_AGENTS = ("gptbot", "chatgpt-user", "claudebot", "anthropic-ai", "perplexitybot",
                  "google-extended", "oai-searchbot", "ccbot")


def _norm(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    if "://" not in u:
        u = "https://" + u
    return u


def read_robots(url: str, timeout: float = 10.0) -> dict:
    """{'fetched': bool, 'blocks_ai': [agents], 'blocks_all': bool}. Never raises."""
    import httpx
    out = {"fetched": False, "blocks_ai": [], "blocks_all": False}
    u = _norm(url)
    if not u:
        return out
    m = re.match(r"^(https?://[^/]+)", u)
    base = m.group(1) if m else u
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True,
                          headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"}) as c:
            r = c.get(base + "/robots.txt")
        if r.status_code != 200 or "<html" in (r.text or "")[:500].lower():
            return out
        out["fetched"] = True
        out["blocks_ai"], out["blocks_all"] = parse_robots(r.text or "")
    except Exception:
        pass
    return out


def blocks_from_robots(text: str) -> list:
    """Agents among the known AI crawlers whose robots.txt group contains `Disallow: /`."""
    return parse_robots(text)[0]


def parse_robots(text: str) -> tuple:
    """(AI agents whose group has `Disallow: /`, whether the `*` group has `Disallow: /`)."""
    txt = (text or "").lower()
    blocked = []
    blocks_all = False
    # Walk groups: a run of user-agent lines followed by rules.
    groups: list[tuple[list, list]] = []
    agents: list = []
    rules: list = []
    for raw in txt.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("user-agent:"):
            if rules:
                groups.append((agents, rules)); agents, rules = [], []
            agents.append(line.split(":", 1)[1].strip())
        elif line.startswith("disallow:") or line.startswith("allow:"):
            rules.append(line)
    if agents:
        groups.append((agents, rules))
    for ags, rls in groups:
        if any(r.startswith("disallow:") and r.split(":", 1)[1].strip() == "/" for r in rls):
            for a in ags:
                if a in _ROBOTS_AGENTS and a not in blocked:
                    blocked.append(a)
                if a == "*":
                    blocks_all = True
    return blocked, blocks_all


def classify(probe: dict, robots: dict, url: str) -> str:
    """Decided by the named crawlers (GPTBot, ChatGPT-User, ClaudeBot, PerplexityBot). The browser
    request only matters when every crawler is turned away: browser in → blocked; browser out too →
    unreachable (nothing can be said). Google-Extended is a robots.txt token, not a crawler, so it
    is ignored here."""
    if not (url or "").strip():
        return "no_website"
    results = probe.get("results") or []
    real = [r for r in results if r.get("name") not in ("Browser", "Google-Extended")]
    if not results or all(r.get("outcome") == "error" for r in results):
        return "unreachable"
    blocked = [r for r in real if r.get("outcome") == "blocked"]
    allowed = [r for r in real if r.get("outcome") == "allowed"]
    if blocked and allowed:
        return "blocked"                     # selective: some AI readers in, others turned away
    if blocked and not allowed:
        return "blocked" if not probe.get("browser_blocked") else "unreachable"
    if not allowed:
        return "unreachable"                 # errors only
    if robots.get("blocks_ai") or robots.get("blocks_all"):
        return "robots"
    return "open"


def scan_site(url: str) -> dict:
    """Probe one site. Returns {url, bucket, blocked: [names], allowed: [names], browser_status,
    statuses: {name: code}, robots_blocks: [agents]}. Never raises."""
    u = _norm(url)
    if not u:
        return {"url": "", "bucket": "no_website", "blocked": [], "allowed": [], "browser_status": None, "statuses": {}, "robots_blocks": []}
    try:
        probe = probe_ai_crawlers(u)
    except Exception:
        probe = {"results": [], "blocked": [], "allowed": [], "browser_blocked": False}
    robots = read_robots(u) if any(r.get("outcome") == "allowed" for r in (probe.get("results") or [])) else {"fetched": False, "blocks_ai": [], "blocks_all": False}
    statuses = {r.get("name"): r.get("status") for r in (probe.get("results") or [])}
    return {
        "url": u,
        "bucket": classify(probe, robots, u),
        "blocked": list(probe.get("blocked") or []),
        "allowed": list(probe.get("allowed") or []),
        "browser_status": statuses.get("Browser"),
        "statuses": {k: v for k, v in statuses.items() if k != "Browser"},
        "browser_blocked": bool(probe.get("browser_blocked")),
        "robots_blocks": (["*"] if robots.get("blocks_all") else []) + list(robots.get("blocks_ai") or []),
    }


def scan_list(rows: list[dict], progress: Optional[Callable[[int, dict], None]] = None, workers: int = 6) -> list[dict]:
    """rows: [{name, city, state, url}] → same rows + scan fields, in input order."""
    out: list[Optional[dict]] = [None] * len(rows)
    def _one(i: int):
        r = rows[i]
        res = scan_site(r.get("url") or "")
        return i, {**r, **res}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futs = [ex.submit(_one, i) for i in range(len(rows))]
        done = 0
        for f in as_completed(futs):
            i, rec = f.result()
            out[i] = rec
            done += 1
            if progress:
                try:
                    progress(done, rec)
                except Exception:
                    pass
    return [r for r in out if r is not None]


def summarize(results: list[dict]) -> dict:
    counts = {b: 0 for b in BUCKETS}
    for r in results:
        counts[r.get("bucket") or "no_website"] = counts.get(r.get("bucket") or "no_website", 0) + 1
    total = len(results)
    tested = total - counts["no_website"] - counts["unreachable"]
    hidden = counts["blocked"] + counts["robots"]
    crawler_hits: dict = {}
    for r in results:
        for n in r.get("blocked") or []:
            crawler_hits[n] = crawler_hits.get(n, 0) + 1
    return {
        "total": total,
        "tested": tested,
        "counts": counts,
        "blocked_pct": round(100.0 * counts["blocked"] / tested, 1) if tested else 0.0,
        "hidden_pct": round(100.0 * hidden / tested, 1) if tested else 0.0,
        "crawler_hits": crawler_hits,
    }
