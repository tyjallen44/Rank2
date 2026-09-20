"""AI Reputation Trend Report — a customer-facing PDF for one tracked entity.

Built from the tracked entity record + its snapshot history (get_entity_trend).
Charts are inline SVG generated here (no CDN, deterministic), the page is rendered
with the same Playwright harness as the other reports, and an optional short
analyst paragraph is written by Claude from the computed numbers only.

Layout (v4 — written to be read, not decoded):
  header · headline sentence · three big tiles · analyst paragraph
  · score chart zoomed to the range that matters, quartile bands, first/last values, notes
  · one pillar chart (four labelled lines) + a bar table with a plain-English line per pillar
  · Google reputation as two facts (rating, reviews) with sparklines
  · snapshots table (same-day re-runs collapsed) · what changed (large moves, quartile
    crossings, notes, setting drift) · methodology.
"""
from __future__ import annotations

import base64
import html
import json
import math
from datetime import date
from pathlib import Path
from typing import Optional

from . import scoring
from .strings import (AIVS_DISCLAIMER, DATA_LIMITATIONS_BLOCK, PRODUCT_SUBTITLE,
                      rebrand_result as _rebrand_for_display)

_TEAL = "#0F4146"
_TEAL2 = "#177B6E"
_PALE = "#EEF7F1"
_INK = "#1F2D30"
_MUTE = "#5A6E72"
_GREEN = "#2e9e5b"
_AMBER = "#d18f1f"
_RED = "#d94f4f"
_NOTE = "#b45309"

# Quartile bands (scoring.grade_from_score thresholds)
_BANDS = [(75, 100, "#e6f4ea", "1st quartile · Top"), (68, 75, "#eef7f1", "2nd quartile · Upper middle"),
          (58, 68, "#fbf3e3", "3rd quartile · Lower middle"), (0, 58, "#fbe9e7", "4th quartile · Bottom")]

_HOSPITAL_PILLARS = [("tier_outcomes", "Outcomes & Safety"), ("tier_credentials", "Credentials & Recognition"),
                     ("tier_experience", "Experience & Reviews"), ("tier_access", "Access & Fit")]
_PRACTICE_PILLARS = [("tier_outcomes", "Practitioner Credentials & Clinical Quality"),
                     ("tier_credentials", "Reviews & Reputation"),
                     ("tier_experience", "Identity & Machine-Readability"), ("tier_access", "Access & Fit")]
_PILLAR_COLORS = ["#0F4146", "#2E9BB3", "#3CB37A", "#B08D57"]     # the app's four pillar colours, print-safe
_BIG_MOVE = 5                                                       # points; smaller moves are noise in "What changed"


def _e(s) -> str:
    return html.escape(str(s if s is not None else ""))


def _logo_html() -> str:
    p = Path(__file__).parent / "assets" / "logo-white.svg"
    if p.exists():
        data = base64.b64encode(p.read_bytes()).decode()
        return f'<img src="data:image/svg+xml;base64,{data}" style="height:26px" alt="RLDatix">'
    return '<div style="color:#fff;font-weight:700;font-size:20px">Pulse</div>'


def _color(v) -> str:
    if not isinstance(v, (int, float)):
        return _MUTE
    return _RED if v < 58 else _AMBER if v < 75 else _GREEN


def _fmt_date(s: str) -> str:
    try:
        return date.fromisoformat(str(s)[:10]).strftime("%b %-d, %Y")
    except Exception:
        return str(s)[:10]


def _fmt_month(s: str) -> str:
    try:
        return date.fromisoformat(str(s)[:10]).strftime("%B")
    except Exception:
        return str(s)[:10]


def _delta_html(d, suffix: str = "", size: str = "") -> str:
    style = f"font-size:{size};" if size else ""
    if d is None:
        return f'<span style="color:{_MUTE};{style}">—</span>'
    if d == 0:
        return f'<span style="color:{_MUTE};{style}">no change</span>'
    col = _GREEN if d > 0 else _RED
    arrow = "▲" if d > 0 else "▼"
    return f'<span style="color:{col};font-weight:700;{style}">{arrow}{abs(d)}{suffix}</span>'


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


# ── Stats ────────────────────────────────────────────────────────────────────
def _collapse_same_day(points: list[dict]) -> list[dict]:
    """One row per day: the last run of the day wins; earlier same-day runs are counted."""
    out: list[dict] = []
    for p in points:
        day = str(p.get("generated_at"))[:10]
        if out and str(out[-1].get("generated_at"))[:10] == day:
            reruns = out[-1].get("_reruns", 0) + 1
            out[-1] = dict(p); out[-1]["_reruns"] = reruns
        else:
            out.append(dict(p))
    return out


def compute_stats(entity: dict, points: list[dict]) -> dict:
    raw_runs = len([p for p in points if p.get("ai_visibility_score") is not None])
    points = _collapse_same_day(points)
    pts = [p for p in points if p.get("ai_visibility_score") is not None]
    latest = pts[-1] if pts else None
    prev = pts[-2] if len(pts) >= 2 else None
    first = pts[0] if pts else None
    practice = bool(latest and str(latest.get("run_profile") or "").startswith("practice_"))
    pillars = _PRACTICE_PILLARS if practice else _HOSPITAL_PILLARS

    def _delta(a, b):
        return (a - b) if (a is not None and b is not None) else None

    pillar_rows = []
    for i, (key, label) in enumerate(pillars):
        f = first.get(key) if first else None
        l = latest.get(key) if latest else None
        pillar_rows.append({"key": key, "label": label, "first": f, "latest": l, "delta": _delta(l, f),
                            "color": _PILLAR_COLORS[i]})
    moves = []
    crossings = []
    for i in range(1, len(pts)):
        d = _delta(pts[i]["ai_visibility_score"], pts[i - 1]["ai_visibility_score"])
        if d:
            moves.append({"date": pts[i]["generated_at"], "delta": d, "score": pts[i]["ai_visibility_score"]})
        q0, _ = scoring.grade_from_score(pts[i - 1]["ai_visibility_score"])
        q1, b1 = scoring.grade_from_score(pts[i]["ai_visibility_score"])
        q_next = scoring.grade_from_score(pts[i + 1]["ai_visibility_score"])[0] if i + 1 < len(pts) else q1
        if q0 != q1 and q_next == q1:
            crossings.append({"date": pts[i]["generated_at"], "from": q0, "to": q1, "label": b1,
                              "up": pts[i]["ai_visibility_score"] > pts[i - 1]["ai_visibility_score"]})
    big_moves = sorted([m for m in moves if abs(m["delta"]) >= _BIG_MOVE], key=lambda m: abs(m["delta"]), reverse=True)
    drift = []
    for p in points:
        flags = []
        if p.get("run_aggregate") is not None and entity.get("aggregate") is not None \
                and bool(p["run_aggregate"]) != bool(entity["aggregate"]):
            flags.append("scope")
        if p.get("run_specialty") and entity.get("specialty") \
                and str(p["run_specialty"]).lower() != str(entity["specialty"]).lower():
            flags.append("specialty")
        if p.get("run_location") and entity.get("city") \
                and not str(p["run_location"]).lower().startswith(str(entity["city"]).lower()):
            flags.append("market")
        if flags:
            drift.append({"date": p.get("generated_at"), "flags": flags})
    code, band = scoring.grade_from_score(latest["ai_visibility_score"] if latest else None)
    up = [r for r in pillar_rows if r["delta"] is not None and r["delta"] > 0]
    down = [r for r in pillar_rows if r["delta"] is not None and r["delta"] < 0]
    scored = [r for r in pillar_rows if r["latest"] is not None]
    biggest = max([r for r in pillar_rows if r["delta"]], key=lambda r: abs(r["delta"]), default=None)
    scores = [p["ai_visibility_score"] for p in pts]
    return {
        "n": len(pts), "runs": raw_runs, "first": first, "latest": latest, "prev": prev,
        "latest_score": latest["ai_visibility_score"] if latest else None,
        "quartile_code": code, "quartile_label": band,
        "delta_prev": _delta(latest and latest["ai_visibility_score"], prev and prev["ai_visibility_score"]),
        "delta_first": _delta(latest and latest["ai_visibility_score"], first and first["ai_visibility_score"]),
        "pillars": pillar_rows, "practice_rubric": practice,
        "best_pillar": max(up, key=lambda r: r["delta"]) if up else None,
        "worst_pillar": min(down, key=lambda r: r["delta"]) if down else None,
        "biggest_mover": biggest,
        "lowest_pillar": min(scored, key=lambda r: r["latest"]) if scored else None,
        "highest_pillar": max(scored, key=lambda r: r["latest"]) if scored else None,
        "moves": moves[:3], "big_moves": big_moves, "crossings": crossings, "drift": drift,
        "range": (min(scores), max(scores)) if scores else (None, None),
        "period": (_fmt_date(first["generated_at"]) if first else "", _fmt_date(latest["generated_at"]) if latest else ""),
    }


def headline(st: dict) -> str:
    """One plain sentence a reader can take away without reading anything else."""
    if st["latest_score"] is None:
        return "No scored snapshot yet."
    d = st["delta_first"]
    first = st["first"]
    if d is None or st["n"] < 2:
        lead = f"Scored {st['latest_score']} — {st['quartile_label']} — at the first snapshot."
    elif d == 0:
        lead = f"Unchanged at {st['latest_score']} since {_fmt_month(first['generated_at'])}, {st['quartile_label']}."
    else:
        lead = (f"{'Up' if d > 0 else 'Down'} {abs(d)} point{'s' if abs(d) != 1 else ''} since "
                f"{_fmt_month(first['generated_at'])}, now {st['quartile_label']} at {st['latest_score']}.")
    bits = []
    bm, low = st["biggest_mover"], st["lowest_pillar"]
    if bm:
        bits.append(f"{bm['label']} moved most ({'+' if bm['delta'] > 0 else ''}{bm['delta']})")
    if low:
        if bm and low["key"] == bm["key"]:
            bits[-1] += " and is still the weakest pillar"
        else:
            bits.append(f"{low['label']} is the weakest pillar at {low['latest']}")
    return lead + (" " + "; ".join(bits) + "." if bits else "")


def pillar_meaning(r: dict, st: dict) -> str:
    """A plain-English line under each pillar, from the numbers only."""
    v = r["latest"]
    if v is None:
        return "Not scored in the latest snapshot."
    if v >= 85:
        phrase = "a clear strength AI assistants can cite"
    elif v >= 75:
        phrase = "a strength — top-quartile territory"
    elif v >= 58:
        phrase = "credible but not yet a differentiator"
    else:
        phrase = "the constraint on the overall score"
    line = f"{phrase.capitalize()}"
    latest = st["latest"] or {}
    is_reviews = r["key"] == ("tier_credentials" if st["practice_rubric"] else "tier_experience")
    if is_reviews and latest.get("google_rating") is not None:
        cnt = latest.get("google_count")
        line += (f" — Google {latest['google_rating']}★"
                 + (f" across {int(cnt):,} reviews" if cnt else "") + " drives this pillar")
    if r["delta"]:
        line += f"; {'up' if r['delta'] > 0 else 'down'} {abs(r['delta'])} since tracking began"
    if st["lowest_pillar"] and r["key"] == st["lowest_pillar"]["key"] and v < 75:
        line += ". This is the lever."
    else:
        line += "."
    return line


# ── Analyst paragraph (numbers-only prompt; fail-soft) ───────────────────────
def analyst_paragraph(entity: dict, stats: dict) -> str:
    try:
        import anthropic
        client = anthropic.Anthropic()
        facts = {
            "entity": entity.get("display_name") or entity.get("entity_name"), "market": f"{entity.get('city')}, {entity.get('state')}",
            "specialty": entity.get("specialty"), "snapshots": stats["n"],
            "period": stats["period"], "latest_score": stats["latest_score"],
            "quartile": stats["quartile_label"], "change_since_previous": stats["delta_prev"],
            "change_since_first": stats["delta_first"],
            "pillars": [{"pillar": r["label"], "first": r["first"], "latest": r["latest"], "change": r["delta"]}
                        for r in stats["pillars"]],
            "largest_moves": stats["moves"],
        }
        resp = client.messages.create(
            model="claude-opus-5",
            max_tokens=600,
            output_config={"effort": "low"},
            system=("You write the executive summary paragraph of an AI Reputation Trend Report for a "
                    "healthcare organization. Use ONLY the numbers provided — never invent causes, events, "
                    "or data. 3–4 sentences, plain business English, no bullet points, no headings, no "
                    "hedging boilerplate. Name the direction of travel, the pillar that moved most, and "
                    "what the current quartile means for how AI assistants present the organization."),
            messages=[{"role": "user", "content": json.dumps(facts, default=str)}],
        )
        if resp.stop_reason == "refusal":
            return ""
        text = "".join(b.text for b in resp.content if b.type == "text").strip()
        return html.escape(text)
    except Exception:
        return ""


# ── SVG charts ───────────────────────────────────────────────────────────────
def _annotation_x(pts: list[dict], note_date: str, x_of_index) -> Optional[float]:
    """Horizontal position of a dated note: interpolated between the snapshots on
    either side of it (clamped to the first/last snapshot)."""
    from datetime import date as _d
    try:
        t = _d.fromisoformat(str(note_date)[:10]).toordinal()
        ds = [_d.fromisoformat(str(p["generated_at"])[:10]).toordinal() for p in pts]
    except Exception:
        return None
    n = len(ds)
    if n == 1 or t <= ds[0]:
        return x_of_index(0)
    if t >= ds[-1]:
        return x_of_index(n - 1)
    for i in range(n - 1):
        if ds[i] <= t <= ds[i + 1]:
            span = ds[i + 1] - ds[i]
            frac = (t - ds[i]) / span if span else 0
            return x_of_index(i) + frac * (x_of_index(i + 1) - x_of_index(i))
    return None


def _zoom_range(values: list, pad: int = 12, min_span: int = 30) -> tuple[int, int]:
    """Y-axis range that shows the movement: the data ± padding, rounded to tens,
    never narrower than min_span, clamped to 0–100."""
    lo, hi = min(values), max(values)
    lo = max(0, int(math.floor((lo - pad) / 10.0) * 10))
    hi = min(100, int(math.ceil((hi + pad) / 10.0) * 10))
    while hi - lo < min_span:
        if lo > 0:
            lo -= 10
        elif hi < 100:
            hi += 10
        else:
            break
    return lo, hi


def _x_labels(pts: list[dict], n: int, x, h: int, out: list, size: float = 10) -> None:
    idx = sorted(set([0, n - 1] + [round((n - 1) * k / 4) for k in (1, 2, 3)])) if n > 5 else list(range(n))
    for i in idx:
        anchor = "start" if i == 0 else "end" if i == n - 1 else "middle"
        out.append(f'<text x="{x(i):.1f}" y="{h-9}" font-size="{size}" fill="{_MUTE}" text-anchor="{anchor}">{_e(_fmt_date(pts[i]["generated_at"]))}</text>')


def _score_chart(points: list[dict], w: int = 700, h: int = 270, annotations: Optional[list] = None) -> str:
    pts = [p for p in points if p.get("ai_visibility_score") is not None]
    if len(pts) < 1:
        return ""
    L, R, T, B = 40, 150, 22, 32
    iw, ih = w - L - R, h - T - B
    n = len(pts)
    vals = [p["ai_visibility_score"] for p in pts]
    lo, hi = _zoom_range(vals)
    x = lambda i: L + (iw * i / (n - 1) if n > 1 else iw / 2)
    y = lambda v: T + ih - ih * (max(lo, min(hi, v)) - lo) / (hi - lo)
    out = [f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" font-family="Inter,Arial,sans-serif">']
    # Quartile bands clipped to the visible range, named on the right.
    for blo, bhi, col, lbl in _BANDS:
        a, b = max(lo, blo), min(hi, bhi)
        if b <= a:
            continue
        out.append(f'<rect x="{L}" y="{y(b):.1f}" width="{iw}" height="{(y(a) - y(b)):.1f}" fill="{col}"/>')
        if y(a) - y(b) >= 14:
            out.append(f'<text x="{L+iw+8}" y="{(y(a)+y(b))/2 + 4:.1f}" font-size="10" fill="{_MUTE}">{lbl}</text>')
    step = 10 if hi - lo <= 50 else 20
    for v in range(lo, hi + 1, step):
        out.append(f'<line x1="{L}" x2="{L+iw}" y1="{y(v):.1f}" y2="{y(v):.1f}" stroke="#cfe0db" stroke-width="0.7"/>')
        out.append(f'<text x="{L-8}" y="{y(v)+4:.1f}" font-size="10" fill="{_MUTE}" text-anchor="end">{v}</text>')
    d = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(vals))
    out.append(f'<polyline points="{d}" fill="none" stroke="{_TEAL2}" stroke-width="3" stroke-linejoin="round" stroke-linecap="round"/>')
    for i, v in enumerate(vals):
        out.append(f'<circle cx="{x(i):.1f}" cy="{y(v):.1f}" r="{5 if i in (0, n-1) else 3}" fill="{_TEAL if i in (0, n-1) else _TEAL2}" stroke="#fff" stroke-width="1.5"/>')
    # First and last values labelled on the chart.
    out.append(f'<text x="{x(0):.1f}" y="{y(vals[0]) - 11:.1f}" font-size="12" font-weight="700" fill="{_TEAL}" text-anchor="{"middle" if n > 1 else "middle"}">{vals[0]}</text>')
    if n > 1:
        out.append(f'<text x="{x(n-1):.1f}" y="{y(vals[-1]) - 11:.1f}" font-size="13" font-weight="800" fill="{_color(vals[-1])}" text-anchor="middle">{vals[-1]}</text>')
    # Numbered note markers (dashed vertical line + badge), matching the Notes list.
    for k, a in enumerate(annotations or []):
        ax = _annotation_x(pts, a.get("note_date"), x)
        if ax is None:
            continue
        out.append(f'<line x1="{ax:.1f}" x2="{ax:.1f}" y1="{T}" y2="{T+ih}" stroke="{_NOTE}" stroke-width="1.2" stroke-dasharray="4,3"/>')
        out.append(f'<circle cx="{ax:.1f}" cy="{T+10}" r="9" fill="{_NOTE}"/>')
        out.append(f'<text x="{ax:.1f}" y="{T+14}" font-size="10" font-weight="700" fill="#fff" text-anchor="middle">{k+1}</text>')
    _x_labels(pts, n, x, h, out)
    out.append("</svg>")
    return "".join(out)


def _pillar_chart(points: list[dict], pillars: list[dict], w: int = 700, h: int = 280) -> str:
    keys = [r["key"] for r in pillars]
    pts = [p for p in points if any(p.get(k) is not None for k in keys)]
    if len(pts) < 2:
        return f'<div style="font-size:11px;color:{_MUTE};padding:30px 0;text-align:center">Not enough snapshots to chart the pillars yet.</div>'
    L, R, T, B = 40, 200, 18, 32
    iw, ih = w - L - R, h - T - B
    n = len(pts)
    vals = [p.get(k) for p in pts for k in keys if p.get(k) is not None]
    lo, hi = _zoom_range(vals, pad=8, min_span=40)
    x = lambda i: L + iw * i / (n - 1)
    y = lambda v: T + ih - ih * (max(lo, min(hi, float(v))) - lo) / (hi - lo)
    out = [f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" font-family="Inter,Arial,sans-serif">']
    step = 10 if hi - lo <= 50 else 20
    for v in range(lo, hi + 1, step):
        out.append(f'<line x1="{L}" x2="{L+iw}" y1="{y(v):.1f}" y2="{y(v):.1f}" stroke="#e3ede9" stroke-width="0.7"/>')
        out.append(f'<text x="{L-8}" y="{y(v)+4:.1f}" font-size="10" fill="{_MUTE}" text-anchor="end">{v}</text>')
    # Right-hand labels: nudge apart so they never overlap.
    ends = []
    for r in pillars:
        series = [(i, p.get(r["key"])) for i, p in enumerate(pts) if p.get(r["key"]) is not None]
        if not series:
            continue
        d = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in series)
        out.append(f'<polyline points="{d}" fill="none" stroke="{r["color"]}" stroke-width="2.6" stroke-linejoin="round" stroke-linecap="round"/>')
        li, lv = series[-1]
        out.append(f'<circle cx="{x(li):.1f}" cy="{y(lv):.1f}" r="4" fill="{r["color"]}" stroke="#fff" stroke-width="1.5"/>')
        ends.append([y(lv), r, lv])
    ends.sort(key=lambda e: e[0])
    for k in range(1, len(ends)):
        if ends[k][0] - ends[k - 1][0] < 15:
            ends[k][0] = ends[k - 1][0] + 15
    for ly, r, lv in ends:
        short = r["label"].replace("Practitioner Credentials & Clinical Quality", "Credentials & Quality") \
                          .replace("Identity & Machine-Readability", "Identity & Readability")
        delta = r["delta"]
        dtxt = "" if not delta else f' ({"+" if delta > 0 else ""}{delta})'
        out.append(f'<text x="{L+iw+10}" y="{ly + 4:.1f}" font-size="10.5" fill="{r["color"]}" font-weight="700">{lv}<tspan fill="{_INK}" font-weight="600"> {_e(short)}</tspan><tspan fill="{_GREEN if (delta or 0) > 0 else _RED}" font-weight="700">{_e(dtxt)}</tspan></text>')
    _x_labels(pts, n, x, h, out)
    out.append("</svg>")
    return "".join(out)


def _spark(values: list, w: int = 200, h: int = 44, color: str = _TEAL2, vmin: Optional[float] = None, vmax: Optional[float] = None) -> str:
    vals = [float(v) for v in values if v is not None]
    if len(vals) < 2:
        return ""
    lo = vmin if vmin is not None else min(vals)
    hi = vmax if vmax is not None else max(vals)
    if hi <= lo:
        hi = lo + 1
    n = len(vals)
    x = lambda i: 4 + (w - 8) * i / (n - 1)
    y = lambda v: 4 + (h - 8) - (h - 8) * (v - lo) / (hi - lo)
    d = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(vals))
    return (f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}">'
            f'<polyline points="{d}" fill="none" stroke="{color}" stroke-width="2.4" stroke-linejoin="round" stroke-linecap="round"/>'
            f'<circle cx="{x(n-1):.1f}" cy="{y(vals[-1]):.1f}" r="3.5" fill="{color}" stroke="#fff" stroke-width="1.2"/></svg>')


def _google_facts(points: list[dict]) -> str:
    pts = [p for p in points if p.get("google_rating") is not None]
    if not pts:
        return f'<div style="font-size:11px;color:{_MUTE};padding:14px 0">No Google reputation snapshots yet.</div>'
    f, l = pts[0], pts[-1]
    ratings = [p["google_rating"] for p in pts]
    counts = [p.get("google_count") for p in pts if p.get("google_count") is not None]
    r_delta = round(float(l["google_rating"]) - float(f["google_rating"]), 1) if len(pts) > 1 else None
    c_delta = (int(counts[-1]) - int(counts[0])) if len(counts) > 1 else None
    r_lo = max(1.0, math.floor(min(ratings) * 2) / 2 - 0.5)
    r_hi = min(5.0, math.ceil(max(ratings) * 2) / 2 + 0.5)
    rating_delta = (_delta_html(r_delta, "★", "13px") if r_delta is not None and r_delta != 0
                    else f'<span style="font-size:13px;color:{_MUTE}">no change</span>')
    count_delta = (_delta_html(c_delta, "", "13px") if c_delta else f'<span style="font-size:13px;color:{_MUTE}">no change</span>')
    return f"""
      <div class="facts">
        <div class="fact">
          <div class="fact-l">Google rating</div>
          <div class="fact-row"><div class="fact-v" style="color:{_TEAL}">{l['google_rating']}★</div><div>{rating_delta}<div class="fact-s">from {f['google_rating']}★ on {_e(_fmt_date(f['generated_at']))}</div></div></div>
          {_spark(ratings, color=_TEAL2, vmin=r_lo, vmax=r_hi)}
        </div>
        <div class="fact">
          <div class="fact-l">Google reviews</div>
          <div class="fact-row"><div class="fact-v" style="color:{_TEAL}">{int(counts[-1]):,}</div><div>{count_delta}<div class="fact-s">{'from ' + format(int(counts[0]), ',') + ' on ' + _e(_fmt_date(f['generated_at'])) if len(counts) > 1 else 'latest snapshot'}</div></div></div>
          {_spark(counts, color="#2E9BB3", vmin=(min(counts) * 0.9 if counts else None))}
        </div>
      </div>""" if counts else f"""
      <div class="facts"><div class="fact"><div class="fact-l">Google rating</div>
        <div class="fact-row"><div class="fact-v" style="color:{_TEAL}">{l['google_rating']}★</div><div>{rating_delta}</div></div>{_spark(ratings, color=_TEAL2, vmin=r_lo, vmax=r_hi)}</div></div>"""


# ── HTML ─────────────────────────────────────────────────────────────────────
def build_trend_html(entity: dict, points: list[dict], *, analyst: Optional[str] = None,
                     prepared_for: str = "", annotations: Optional[list] = None) -> str:
    annotations = list(annotations or [])
    st = compute_stats(entity, points)
    points = _collapse_same_day(points)
    today = date.today().strftime("%B %-d, %Y")
    etype = {"practice": "Specialty Practice", "service_line": "Hospital Service Line",
             "community_health": "Community Health"}.get(str(entity.get("entity_type") or ""), "Hospital")
    sub_bits = [f"{_e(entity.get('city'))}, {_e(entity.get('state'))}", _e(etype)]
    if entity.get("specialty"):
        sub_bits.append(_e(entity["specialty"]))
    sub_bits.append("All locations rolled up" if entity.get("aggregate") else "Single location")
    if st["period"][0]:
        sub_bits.append(f"Tracking since {_e(st['period'][0])} · {_plural(st['n'], 'snapshot')}" + (f" ({st['runs']} runs)" if st["runs"] > st["n"] else ""))
    sub = " &nbsp;·&nbsp; ".join(sub_bits)

    latest = st["latest_score"]
    q_col = _color(latest)
    latest_html = (f'<div class="tile-v" style="color:{q_col}">{latest}<span class="tile-out">/100</span></div>'
                   f'<div class="tile-badge" style="background:{q_col}">{_e(st["quartile_label"])}</div>') \
        if latest is not None else f'<div style="color:{_MUTE};font-size:14px">No scored snapshot yet</div>'
    first_date = st["period"][0]
    change_html = (f'<div class="tile-v">{_delta_html(st["delta_first"], "", "34px")}</div>'
                   f'<div class="tile-s">since {_e(first_date)}' + (f' · {_delta_html(st["delta_prev"])} since the previous snapshot' if st["delta_prev"] is not None else '') + '</div>') \
        if st["n"] >= 2 else f'<div class="tile-s" style="margin-top:8px">First snapshot — no change to report yet.</div>'
    bm = st["biggest_mover"]
    mover_html = (f'<div class="tile-v" style="font-size:22px;line-height:1.15;color:{_INK}">{_e(bm["label"])}</div>'
                  f'<div class="tile-s">{_delta_html(bm["delta"], "", "15px")} &nbsp;{bm["first"]} → <b>{bm["latest"]}</b></div>') \
        if bm else f'<div class="tile-s" style="margin-top:8px">No pillar has moved yet.</div>'

    # Pillar table with bars + meaning lines.
    pillar_rows = []
    for r in st["pillars"]:
        v = r["latest"]
        bar = (f'<div class="bar"><div class="bar-fill" style="width:{max(2, min(100, v))}%;background:{_color(v)}"></div></div>'
               if v is not None else '')
        pillar_rows.append(
            f'<tr><td style="width:34%"><div class="pl"><span class="dot" style="background:{r["color"]}"></span>{_e(r["label"])}</div>'
            f'<div class="pm">{_e(pillar_meaning(r, st))}</div></td>'
            f'<td style="width:38%;vertical-align:middle">{bar}</td>'
            f'<td class="num" style="color:{_MUTE}">{r["first"] if r["first"] is not None else "—"}</td>'
            f'<td class="num" style="font-weight:800;color:{_color(v)};font-size:15px">{v if v is not None else "—"}</td>'
            f'<td class="num">{_delta_html(r["delta"], "", "12px")}</td></tr>')

    rubrics = sorted({(p.get("rubric") or "hospital") for p in points})
    mixed = len(rubrics) > 1
    snap_rows = []
    prev = None
    for p in points:
        s = p.get("ai_visibility_score")
        d = (s - prev) if (s is not None and prev is not None) else None
        prev = s if s is not None else prev
        settings = " · ".join(x for x in [
            (None if p.get("run_aggregate") is None else ("All locations" if p.get("run_aggregate") else "Single location")),
            p.get("run_specialty"), p.get("run_location")] if x)
        flagged = any(dr["date"] == p.get("generated_at") for dr in st["drift"])
        delta_cell = _delta_html(d) if d is not None else f'<span style="color:{_MUTE}">—</span>'
        pillar_cells = "".join('<td class="num">%s</td>' % (p.get(r["key"]) if p.get(r["key"]) is not None else "—")
                               for r in st["pillars"])
        g_rating = (str(p["google_rating"]) + "★") if p.get("google_rating") is not None else "—"
        g_count = f"{int(p['google_count']):,}" if p.get("google_count") is not None else "—"
        extra = ""
        if p.get("_reruns"):
            extra += f' <span class="tag">re-run ×{p["_reruns"] + 1}</span>'
        if flagged:
            extra += f' <span style="color:{_NOTE};font-weight:700" title="Settings differed">⚠</span>'
        if mixed:
            rb = p.get("rubric") or "hospital"
            extra += f' <span class="tag">{"practice rubric" if rb == "practice" else "hospital rubric"}</span>'
        snap_rows.append(
            f'<tr><td style="white-space:nowrap">{_e(_fmt_date(p.get("generated_at")))}{extra}</td>'
            f'<td class="num" style="font-weight:800;color:{_color(s)}">{s if s is not None else "—"}</td>'
            f'<td class="num">{delta_cell}</td>{pillar_cells}'
            f'<td class="num">{g_rating}</td><td class="num">{g_count}</td>'
            f'<td style="font-size:9.5px;color:{_MUTE}">{_e(settings) or "—"}</td></tr>')
    pillar_ths = "".join('<th class="num">%s</th>' % _e(r["label"].split(" & ")[0].replace("Practitioner Credentials", "Credentials")) for r in st["pillars"])

    # What changed: big moves, quartile crossings, notes, setting drift.
    changes = []
    for c in st["crossings"]:
        changes.append(f'<li><b>{_e(_fmt_date(c["date"]))}</b> — {"moved up into" if c["up"] else "dropped into"} the <b>{_e(c["label"])}</b> quartile.</li>')
    for m in st["big_moves"]:
        changes.append(f'<li><b>{_e(_fmt_date(m["date"]))}</b> — score moved {_delta_html(m["delta"])} to {m["score"]}.</li>')
    for k, a in enumerate(annotations):
        changes.append(f'<li><span class="nbadge">{k+1}</span><b>{_e(_fmt_date(a.get("note_date")))}</b> — {_e(a.get("note", ""))}</li>')
    for dr in st["drift"]:
        changes.append(f'<li><b>{_e(_fmt_date(dr["date"]))}</b> — run settings differed from the current configuration ({_e(", ".join(dr["flags"]))}); treat that point with care.</li>')
    if mixed:
        first_p = next((p for p in points if (p.get("rubric") or "hospital") == "practice"), None)
        changes.append('<li><b>Rubric change</b> — earlier snapshots were scored on the hospital rubric and later ones on the practice rubric'
                       + (f' (from {_e(_fmt_date(first_p["generated_at"]))})' if first_p else '') + '; scores are comparable only within a rubric.</li>')
    if not changes:
        lo, hi = st["range"]
        span = (hi - lo) if (lo is not None and hi is not None) else None
        changes.append(f'<li style="color:{_MUTE}">No large moves. The score has stayed within {span} point{"s" if span != 1 else ""} ({lo}–{hi}) over the period; moves under {_BIG_MOVE} points are ordinary run-to-run variation.</li>'
                       if span is not None else f'<li style="color:{_MUTE}">Nothing to report yet.</li>')

    limitations_html = _e(DATA_LIMITATIONS_BLOCK).replace("\n\n", "<br><br>")
    lo_r, hi_r = _zoom_range([p["ai_visibility_score"] for p in points if p.get("ai_visibility_score") is not None]) if st["n"] else (0, 100)

    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
      * {{ box-sizing:border-box; margin:0; padding:0; }}
      body {{ font-family:'Inter','Helvetica Neue',Arial,sans-serif; color:{_INK}; font-size:11.5px; line-height:1.5; }}
      .band {{ background:{_TEAL}; color:#fff; padding:18px 40px 20px; }}
      .band .top {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:12px; }}
      .band .kick {{ font-size:10px; letter-spacing:.12em; text-transform:uppercase; color:#9FD8CF; }}
      .band h1 {{ font-size:30px; font-weight:800; line-height:1.1; margin:4px 0 6px; }}
      .band .sub {{ font-size:12px; color:#CFEAE6; }}
      .headline {{ margin:20px 40px 0; font-size:17px; font-weight:700; line-height:1.35; color:{_TEAL}; }}
      h2 {{ font-size:15px; font-weight:800; color:{_TEAL}; margin:26px 40px 10px; }}
      .sec {{ padding:0 40px; }}
      .tiles {{ display:grid; grid-template-columns:1fr 1fr 1.3fr; gap:14px; margin-top:14px; }}
      .tile {{ background:{_PALE}; border-radius:10px; padding:14px 16px 14px; min-height:96px; }}
      .tile-l {{ font-size:10.5px; font-weight:700; color:{_MUTE}; margin-bottom:6px; }}
      .tile-v {{ font-size:40px; font-weight:800; line-height:1; }}
      .tile-out {{ font-size:14px; color:{_MUTE}; font-weight:600; margin-left:2px; }}
      .tile-badge {{ display:inline-block; margin-top:8px; padding:3px 10px; border-radius:999px; color:#fff; font-size:10.5px; font-weight:700; }}
      .tile-s {{ font-size:11px; color:{_MUTE}; margin-top:8px; line-height:1.4; }}
      .analyst {{ margin-top:14px; padding:14px 18px; border-left:4px solid {_TEAL2}; background:#fafcfb; font-size:12.5px; line-height:1.6; }}
      .chart {{ background:#fff; border:1px solid #e3ede9; border-radius:10px; padding:10px 10px 4px; }}
      .cap {{ font-size:10px; color:{_MUTE}; margin-top:6px; }}
      table.t {{ width:100%; border-collapse:collapse; font-size:11px; margin-top:10px; }}
      table.t th {{ background:{_TEAL}; color:#fff; font-weight:700; font-size:10px; padding:8px 8px; text-align:left; }}
      table.t td {{ padding:8px 8px; border-bottom:1px solid #e6efec; vertical-align:top; }}
      table.t tr:nth-child(even) td {{ background:#fafcfb; }}
      table.t .num, table.t th.num {{ text-align:center; white-space:nowrap; }}
      table.pillars td {{ padding:10px 8px; }}
      table.snap td:last-child {{ white-space:nowrap; }}
      .pl {{ font-weight:700; font-size:12px; display:flex; align-items:center; gap:7px; }}
      .dot {{ width:10px; height:10px; border-radius:50%; display:inline-block; flex:0 0 auto; }}
      .pm {{ font-size:10.5px; color:{_MUTE}; margin-top:3px; line-height:1.4; }}
      .bar {{ height:12px; background:#e9f0ee; border-radius:6px; overflow:hidden; margin-top:6px; }}
      .bar-fill {{ height:100%; border-radius:6px; }}
      .facts {{ display:grid; grid-template-columns:1fr 1fr; gap:14px; }}
      .fact {{ background:#fff; border:1px solid #e3ede9; border-radius:10px; padding:14px 16px; }}
      .fact-l {{ font-size:10.5px; font-weight:700; color:{_MUTE}; margin-bottom:6px; }}
      .fact-row {{ display:flex; align-items:center; gap:14px; margin-bottom:6px; }}
      .fact-v {{ font-size:30px; font-weight:800; line-height:1; }}
      .fact-s {{ font-size:10px; color:{_MUTE}; }}
      ul.changes {{ margin:6px 0 0 20px; line-height:1.7; font-size:11.5px; }}
      .nbadge {{ display:inline-block; min-width:17px; height:17px; line-height:17px; border-radius:9px; background:{_NOTE}; color:#fff; font-size:9.5px; font-weight:700; text-align:center; margin-right:7px; }}
      .tag {{ font-size:8.5px; font-weight:700; padding:1px 6px; border-radius:8px; background:{_PALE}; color:{_TEAL}; margin-left:4px; vertical-align:middle; }}
      .note {{ margin:20px 40px 24px; padding:14px 18px; background:{_PALE}; border-radius:10px; font-size:10px; color:{_MUTE}; line-height:1.55; }}
      .note b {{ color:{_TEAL}; }}
      .pb {{ break-before:page; page-break-before:always; }}
      .blk {{ break-inside:avoid; page-break-inside:avoid; }}
      h2 {{ break-after:avoid; page-break-after:avoid; }}
      table.t thead {{ display:table-header-group; }}
      table.t tr {{ break-inside:avoid; page-break-inside:avoid; }}
      .chart, .tile, .analyst, .note, .fact {{ break-inside:avoid; page-break-inside:avoid; }}
    </style></head><body>
      <div class="band">
        <div class="top">{_logo_html()}<div style="text-align:right;font-size:10px;letter-spacing:.1em;color:#9FD8CF">AI REPUTATION TREND REPORT<br><span style="letter-spacing:0;color:#CFEAE6">Prepared {_e(today)}{(" for " + _e(prepared_for)) if prepared_for else ""}</span></div></div>
        <div class="kick">{_e(PRODUCT_SUBTITLE)}</div>
        <h1>{_e(entity.get("display_name") or entity.get("entity_name"))}</h1>
        <div class="sub">{sub}{(" &nbsp;·&nbsp; tracked as " + _e(entity.get("entity_name"))) if entity.get("display_name") and str(entity.get("display_name")).strip().lower() != str(entity.get("entity_name") or "").strip().lower() else ""}</div>
      </div>

      <div class="headline">{_e(headline(st))}</div>
      <section class="blk">
      <div class="sec">
        <div class="tiles">
          <div class="tile"><div class="tile-l">Latest Pulse Score</div>{latest_html}</div>
          <div class="tile"><div class="tile-l">Change since tracking began</div>{change_html}</div>
          <div class="tile"><div class="tile-l">Pillar that moved most</div>{mover_html}</div>
        </div>
        {f'<div class="analyst">{analyst}</div>' if analyst else ''}
      </div>
      </section>

      <section class="blk">
      <h2>Pulse Score over time</h2>
      <div class="sec"><div class="chart">{_score_chart(points, annotations=annotations)}</div>
        <div class="cap">Shaded bands are the national quartiles (1st: 75+ · 2nd: 68–74 · 3rd: 58–67 · 4th: below 58). The axis shows {lo_r}–{hi_r} so the movement is visible.{" Numbered markers are the notes listed under What changed." if annotations else ""}</div></div>
      </section>

      <section class="blk">
      <h2>What changed</h2>
      <div class="sec"><ul class="changes">{"".join(changes)}</ul></div>
      </section>

      <section class="blk">
      <h2>The four pillars</h2>
      <div class="sec">
        <div class="chart">{_pillar_chart(points, st["pillars"])}</div>
        <table class="t pillars"><thead><tr><th>Pillar</th><th>Latest score</th><th class="num">First</th><th class="num">Latest</th><th class="num">Change</th></tr></thead>
        <tbody>{"".join(pillar_rows)}</tbody></table>
        <div class="cap">{"Scored on the practice rubric. " if st["practice_rubric"] else ""}Green 75+ · amber 58–74 · red below 58.</div>
      </div>
      </section>

      <section class="blk">
      <h2>Google reputation</h2>
      <div class="sec">{_google_facts(points)}</div>
      </section>


      <h2 class="pb" style="margin-top:0;padding-top:26px">Every snapshot</h2>
      <div class="sec">
        <table class="t snap"><thead><tr><th>Date</th><th class="num">Score</th><th class="num">Change</th>{pillar_ths}
          <th class="num">Google</th><th class="num">Reviews</th><th>Run settings</th></tr></thead>
        <tbody>{"".join(snap_rows)}</tbody></table>
        <div class="cap">Same-day re-runs are collapsed to the last run of the day.</div>
      </div>

      <div class="note"><b>About the Pulse Score.</b> {_e(AIVS_DISCLAIMER)}<br><br>{limitations_html}</div>
    </body></html>"""


def _notes_list_html(annotations: list) -> str:
    """Kept for callers that embed the notes list on their own; the report now lists
    notes under 'What changed'."""
    if not annotations:
        return ""
    items = "".join(
        f'<li><span class="nbadge">{k+1}</span><b>{_e(_fmt_date(a.get("note_date")))}</b> &mdash; {_e(a.get("note", ""))}</li>'
        for k, a in enumerate(annotations))
    return f'<ul class="changes" style="list-style:none;margin-left:0">{items}</ul>'


def render_trend_report_pdf(entity: dict, points: list[dict], pdf_path: str, *,
                            brand: str = "original", with_analyst: bool = True,
                            prepared_for: str = "", annotations: Optional[list] = None) -> None:
    """Render the Trend Report PDF for one tracked entity."""
    _rebrand_for_display(entity)
    _rebrand_for_display(points)
    analyst = analyst_paragraph(entity, compute_stats(entity, points)) if with_analyst else ""
    html_str = build_trend_html(entity, points, analyst=analyst, prepared_for=prepared_for, annotations=annotations)
    from playwright.sync_api import sync_playwright
    Path(pdf_path).parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content(html_str, wait_until="networkidle")
        page.pdf(
            path=str(pdf_path), format="A4",
            margin={"top": "0", "bottom": "0.6in", "left": "0", "right": "0"},
            print_background=True, display_header_footer=True,
            header_template="<span></span>",
            footer_template=(
                '<div style="width:100%;font-family:Arial,Helvetica,sans-serif;font-size:9px;color:#8a9aaa;'
                'display:flex;justify-content:space-between;align-items:center;padding:0 40px 10px;box-sizing:border-box">'
                '<span>Prepared by Pulse | RLDatix &nbsp;&mdash;&nbsp; Confidential</span>'
                '<span>Page <span class="pageNumber"></span> of <span class="totalPages"></span></span></div>'
            ),
        )
        browser.close()
