from perception.data.ai_access_scan import blocks_from_robots, classify, parse_robots, summarize


def _probe(blocked=(), allowed=("GPTBot",), browser_blocked=False, results=None):
    if results is None:
        results = [{"name": n, "status": 403, "outcome": "blocked"} for n in blocked] + \
                  [{"name": n, "status": 200, "outcome": "allowed"} for n in allowed] + \
                  [{"name": "Browser", "status": 403 if browser_blocked else 200, "outcome": "blocked" if browser_blocked else "allowed"}]
    return {"results": results, "blocked": list(blocked), "allowed": list(allowed), "browser_blocked": browser_blocked}


def test_robots_group_parsing_only_flags_ai_groups_with_root_disallow():
    txt = """
    User-agent: *
    Disallow: /portal/

    User-agent: GPTBot
    User-agent: ClaudeBot
    Disallow: /

    User-agent: PerplexityBot
    Disallow: /private/
    """
    assert blocks_from_robots(txt) == ["gptbot", "claudebot"]
    assert parse_robots(txt)[1] is False                       # '*' group only disallows /portal/
    assert parse_robots("User-agent: *\nDisallow: /\n")[1] is True


def test_classify_buckets():
    assert classify(_probe(), {}, "") == "no_website"
    assert classify(_probe(browser_blocked=True, blocked=("GPTBot", "ClaudeBot"), allowed=()), {}, "https://x.org") == "unreachable"
    # browser challenged (WAF fingerprinting our client) but the named crawlers get in → open, not unreachable
    assert classify(_probe(browser_blocked=True, allowed=("GPTBot", "ClaudeBot")), {"blocks_ai": []}, "https://x.org") == "open"
    # selective block is a block even when our browser request is challenged too
    assert classify(_probe(browser_blocked=True, blocked=("ClaudeBot",), allowed=("GPTBot",)), {}, "https://x.org") == "blocked"
    # Google-Extended alone never decides
    p = _probe(allowed=("GPTBot",)); p["results"].append({"name": "Google-Extended", "status": 403, "outcome": "blocked"})
    assert classify(p, {"blocks_ai": []}, "https://x.org") == "open"
    assert classify(_probe(results=[{"name": "GPTBot", "status": None, "outcome": "error"}, {"name": "Browser", "status": None, "outcome": "error"}]), {}, "https://x.org") == "unreachable"
    assert classify(_probe(blocked=("GPTBot", "ClaudeBot")), {"blocks_ai": []}, "https://x.org") == "blocked"
    assert classify(_probe(), {"blocks_ai": ["gptbot"], "blocks_all": False}, "https://x.org") == "robots"
    assert classify(_probe(), {"blocks_ai": [], "blocks_all": False}, "https://x.org") == "open"


def test_summary_percentages_exclude_untestable_rows():
    res = [{"bucket": "blocked", "blocked": ["GPTBot", "ClaudeBot"]}, {"bucket": "robots", "blocked": []},
           {"bucket": "open", "blocked": []}, {"bucket": "open", "blocked": []},
           {"bucket": "unreachable", "blocked": []}, {"bucket": "no_website", "blocked": []}]
    s = summarize(res)
    assert s["total"] == 6 and s["tested"] == 4
    assert s["blocked_pct"] == 25.0 and s["hidden_pct"] == 50.0
    assert s["crawler_hits"] == {"GPTBot": 1, "ClaudeBot": 1}
