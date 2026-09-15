"""AI Reputation Trend Report — a customer-facing PDF for one tracked entity.

Built from the tracked entity record + its snapshot history (get_entity_trend).
Charts are inline SVG generated here (no CDN, deterministic), the page is rendered
with the same Playwright harness as the other reports, and an optional short
analyst paragraph is written by Claude from the computed numbers only.

Sections: cover band · executive summary · score trend (quartile bands) ·
pillar trends (small multiples + first/latest/change table) · Google reputation
over time · snapshot table (with the settings each run used) · notable changes ·
methodology + disclaimer.
"""
from __future__ import annotations

import base64
import html
import json
from datetime import date
from pathlib import Path
from typing import Optional

from . import scoring
from .strings import (AIVS_DISCLAIMER, DATA_LIMITATIONS_BLOCK, PRODUCT_SUBTITLE,
                      rebrand_result as _rebrand_for_display)

_TEAL = "#0F4146"
_TEAL2 = "#177B6E"
_PALE = "#EEF7F1"
_INK = "#2B3A3D"
_MUTE = "#5A6E72"
_GREEN = "#2e9e5b"
_AMBER = "#e09b2a"
_RED = "#d94f4f"

# Quartile bands (scoring.grade_from_score thresholds)
_BANDS = [(75, 100, "#e6f4ea", "1st Quartile"), (68, 75, "#eef7f1", "2nd Quartile"),
          (58, 68, "#fbf3e3", "3rd Quartile"), (0, 58, "#fbe9e7", "4th Quartile")]

_HOSPITAL_PILLARS = [("tier_outcomes", "Outcomes & Safety"), ("tier_credentials", "Credentials & Recognition"),
                     ("tier_experience", "Experience & Reviews"), ("tier_access", "Access & Fit")]
_PRACTICE_PILLARS = [("tier_outcomes", "Practitioner Credentials & Clinical Quality"),
                     ("tier_credentials", "Reviews & Reputation"),
                     ("tier_experience", "Identity & Machine-Readability"), ("tier_access", "Access & Fit")]


def _e(s) -> str:
    return html.escape(str(s if s is not None else ""))


def _logo_html() -> str:
    p = Path(__file__).parent / "assets" / "logo-white.svg"
    if p.exists():
        data = base64.b64encode(p.read_bytes()).decode()
        return f'<img src="data:image/svg+xml;base64,{data}" style="height:34px" alt="RLDatix">'
    return '<div style="color:#fff;font-weight:700;font-size:22px">Pulse</div>'


def _color(v) -> str:
    if not isinstance(v, (int, float)):
        return _MUTE
    return _RED if v < 58 else _AMBER if v < 75 else _GREEN


def _fmt_date(s: str) -> str:
    try:
        return date.fromisoformat(str(s)[:10]).strftime("%b %-d, %Y")
    except Exception:
        return str(s)[:10]


def _delta_html(d, suffix: str = "") -> str:
    if d is None:
        return '<span style="color:%s">—</span>' % _MUTE
    if d == 0:
        return '<span style="color:%s">no change</span>' % _MUTE
    col = _GREEN if d > 0 else _RED
    arrow = "▲" if d > 0 else "▼"
    return f'<span style="color:{col};font-weight:700">{arrow}{abs(d)}{suffix}</span>'


# ── Stats ────────────────────────────────────────────────────────────────────
def compute_stats(entity: dict, points: list[dict]) -> dict:
    pts = [p for p in points if p.get("ai_visibility_score") is not None]
    latest = pts[-1] if pts else None
    prev = pts[-2] if len(pts) >= 2 else None
    first = pts[0] if pts else None
    practice = bool(latest and str(latest.get("run_profile") or "").startswith("practice_"))
    pillars = _PRACTICE_PILLARS if practice else _HOSPITAL_PILLARS

    def _delta(a, b):
        return (a - b) if (a is not None and b is not None) else None

    pillar_rows = []
    for key, label in pillars:
        f = first.get(key) if first else None
        l = latest.get(key) if latest else None
        pillar_rows.append({"key": key, "label": label, "first": f, "latest": l, "delta": _delta(l, f)})
    moves = []
    for i in range(1, len(pts)):
        d = _delta(pts[i]["ai_visibility_score"], pts[i - 1]["ai_visibility_score"])
        if d:
            moves.append({"date": pts[i]["generated_at"], "delta": d, "score": pts[i]["ai_visibility_score"]})
    moves.sort(key=lambda m: abs(m["delta"]), reverse=True)
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
    return {
        "n": len(pts), "first": first, "latest": latest, "prev": prev,
        "latest_score": latest["ai_visibility_score"] if latest else None,
        "quartile_code": code, "quartile_label": band,
        "delta_prev": _delta(latest and latest["ai_visibility_score"], prev and prev["ai_visibility_score"]),
        "delta_first": _delta(latest and latest["ai_visibility_score"], first and first["ai_visibility_score"]),
        "pillars": pillar_rows, "practice_rubric": practice,
        "best_pillar": max(up, key=lambda r: r["delta"]) if up else None,
        "worst_pillar": min(down, key=lambda r: r["delta"]) if down else None,
        "moves": moves[:3], "drift": drift,
        "period": (_fmt_date(first["generated_at"]) if first else "", _fmt_date(latest["generated_at"]) if latest else ""),
    }


# ── Analyst paragraph (numbers-only prompt; fail-soft) ───────────────────────
def analyst_paragraph(entity: dict, stats: dict) -> str:
    try:
        import anthropic
        client = anthropic.Anthropic()
        facts = {
            "entity": entity.get("entity_name"), "market": f"{entity.get('city')}, {entity.get('state')}",
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
def _score_chart(points: list[dict], w: int = 640, h: int = 220) -> str:
    pts = [p for p in points if p.get("ai_visibility_score") is not None]
    if len(pts) < 1:
        return ""
    L, R, T, B = 34, 12, 10, 26
    iw, ih = w - L - R, h - T - B
    n = len(pts)
    x = lambda i: L + (iw * i / (n - 1) if n > 1 else iw / 2)
    y = lambda v: T + ih - ih * max(0, min(100, v)) / 100
    out = [f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" font-family="Inter,Arial,sans-serif">']
    for lo, hi, col, lbl in _BANDS:
        band_h = y(lo) - y(hi)
        out.append(f'<rect x="{L}" y="{y(hi):.1f}" width="{iw}" height="{band_h:.1f}" fill="{col}"/>')
        if band_h >= 16:   # skip labels on bands too thin to hold one (the 68–74 band)
            out.append(f'<text x="{L+iw-4}" y="{y(hi)+11:.1f}" font-size="8" fill="{_MUTE}" text-anchor="end">{lbl}</text>')
    for v in (0, 25, 50, 75, 100):
        out.append(f'<line x1="{L}" x2="{L+iw}" y1="{y(v):.1f}" y2="{y(v):.1f}" stroke="#d7e7e2" stroke-width="0.6"/>')
        out.append(f'<text x="{L-6}" y="{y(v)+3:.1f}" font-size="8.5" fill="{_MUTE}" text-anchor="end">{v}</text>')
    d = " ".join(f"{x(i):.1f},{y(p['ai_visibility_score']):.1f}" for i, p in enumerate(pts))
    out.append(f'<polyline points="{d}" fill="none" stroke="{_TEAL2}" stroke-width="2.2" stroke-linejoin="round"/>')
    for i, p in enumerate(pts):
        out.append(f'<circle cx="{x(i):.1f}" cy="{y(p["ai_visibility_score"]):.1f}" r="3" fill="{_TEAL}"/>')
    # x labels: first, last, and up to 3 in between
    idx = sorted(set([0, n - 1] + [round(n * k / 4) for k in (1, 2, 3)] if n > 5 else range(n)))
    for i in idx:
        anchor = "start" if i == 0 else "end" if i == n - 1 else "middle"
        out.append(f'<text x="{x(i):.1f}" y="{h-8}" font-size="8.5" fill="{_MUTE}" text-anchor="{anchor}">{_e(_fmt_date(pts[i]["generated_at"]))}</text>')
    out.append("</svg>")
    return "".join(out)


def _small_chart(points: list[dict], key: str, w: int = 300, h: int = 110, vmax: float = 100) -> str:
    pts = [p for p in points if p.get(key) is not None]
    if len(pts) < 2:
        return f'<div style="font-size:10px;color:{_MUTE};padding:30px 0;text-align:center">Not enough snapshots</div>'
    L, R, T, B = 26, 8, 8, 6
    iw, ih = w - L - R, h - T - B
    n = len(pts)
    x = lambda i: L + iw * i / (n - 1)
    y = lambda v: T + ih - ih * max(0, min(vmax, float(v))) / vmax
    out = [f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" font-family="Inter,Arial,sans-serif">']
    for frac in (0, 0.5, 1):
        v = vmax * frac
        out.append(f'<line x1="{L}" x2="{L+iw}" y1="{y(v):.1f}" y2="{y(v):.1f}" stroke="#e3ede9" stroke-width="0.6"/>')
        out.append(f'<text x="{L-4}" y="{y(v)+3:.1f}" font-size="7.5" fill="{_MUTE}" text-anchor="end">{v:g}</text>')
    d = " ".join(f"{x(i):.1f},{y(p[key]):.1f}" for i, p in enumerate(pts))
    out.append(f'<polyline points="{d}" fill="none" stroke="{_TEAL2}" stroke-width="1.8" stroke-linejoin="round"/>')
    out.append(f'<circle cx="{x(n-1):.1f}" cy="{y(pts[-1][key]):.1f}" r="2.6" fill="{_TEAL}"/>')
    out.append("</svg>")
    return "".join(out)


def _google_chart(points: list[dict], w: int = 640, h: int = 160) -> str:
    pts = [p for p in points if p.get("google_rating") is not None]
    if len(pts) < 2:
        return f'<div style="font-size:10px;color:{_MUTE};padding:20px 0">Not enough Google snapshots to chart.</div>'
    L, R, T, B = 30, 78, 10, 24
    iw, ih = w - L - R, h - T - B
    n = len(pts)
    x = lambda i: L + iw * i / (n - 1)
    yr = lambda v: T + ih - ih * max(0, min(5, float(v))) / 5
    cmax = (max((p.get("google_count") or 0) for p in pts) or 1) * 1.3   # headroom so a flat count isn't glued to the top
    yc = lambda v: T + ih - ih * (float(v or 0)) / cmax
    out = [f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" font-family="Inter,Arial,sans-serif">']
    for v in (1, 2, 3, 4, 5):
        out.append(f'<line x1="{L}" x2="{L+iw}" y1="{yr(v):.1f}" y2="{yr(v):.1f}" stroke="#e3ede9" stroke-width="0.6"/>')
        out.append(f'<text x="{L-4}" y="{yr(v)+3:.1f}" font-size="8" fill="{_MUTE}" text-anchor="end">{v}★</text>')
    dc = " ".join(f"{x(i):.1f},{yc(p.get('google_count')):.1f}" for i, p in enumerate(pts))
    out.append(f'<polyline points="{dc}" fill="none" stroke="#9FD8CF" stroke-width="2" stroke-dasharray="4 3"/>')
    dr = " ".join(f"{x(i):.1f},{yr(p['google_rating']):.1f}" for i, p in enumerate(pts))
    out.append(f'<polyline points="{dr}" fill="none" stroke="{_TEAL2}" stroke-width="2.2"/>')
    last_c = pts[-1].get("google_count") or 0
    out.append(f'<text x="{L+iw+4}" y="{yc(last_c)+3:.1f}" font-size="8" fill="{_MUTE}">{int(last_c):,} reviews</text>')
    out.append(f'<text x="{x(0):.1f}" y="{h-8}" font-size="8.5" fill="{_MUTE}" text-anchor="start">{_e(_fmt_date(pts[0]["generated_at"]))}</text>')
    out.append(f'<text x="{x(n-1):.1f}" y="{h-8}" font-size="8.5" fill="{_MUTE}" text-anchor="end">{_e(_fmt_date(pts[-1]["generated_at"]))}</text>')
    out.append("</svg>")
    return "".join(out)


# ── HTML ─────────────────────────────────────────────────────────────────────
def build_trend_html(entity: dict, points: list[dict], *, analyst: Optional[str] = None,
                     prepared_for: str = "") -> str:
    st = compute_stats(entity, points)
    today = date.today().strftime("%B %-d, %Y")
    sub = f"{_e(entity.get('city'))}, {_e(entity.get('state'))}" + (f" &middot; {_e(entity['specialty'])}" if entity.get("specialty") else "")
    scope = "All locations rolled up" if entity.get("aggregate") else "Single location"

    latest = st["latest_score"]
    latest_html = (f'<div style="font-size:44px;font-weight:800;color:{_color(latest)};line-height:1">{latest}</div>'
                   f'<div style="font-size:10px;color:{_MUTE};margin-top:4px">{_e(st["quartile_label"])}</div>') \
        if latest is not None else f'<div style="color:{_MUTE}">No scored snapshot yet</div>'

    pillar_rows = "".join(
        f'<tr><td>{_e(r["label"])}</td>'
        f'<td style="text-align:center;color:{_color(r["first"])};font-weight:600">{r["first"] if r["first"] is not None else "—"}</td>'
        f'<td style="text-align:center;color:{_color(r["latest"])};font-weight:700">{r["latest"] if r["latest"] is not None else "—"}</td>'
        f'<td style="text-align:center">{_delta_html(r["delta"])}</td></tr>'
        for r in st["pillars"])
    smalls = "".join(
        f'<div class="small"><div class="small-h">{_e(r["label"])}</div>{_small_chart(points, r["key"])}</div>'
        for r in st["pillars"])

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
        dash = '<span style="color:%s">—</span>' % _MUTE
        delta_cell = _delta_html(d) if d is not None else dash
        pillar_cells = "".join('<td style="text-align:center">%s</td>' % (p.get(r["key"]) if p.get(r["key"]) is not None else "—")
                               for r in st["pillars"])
        g_rating = (str(p["google_rating"]) + "★") if p.get("google_rating") is not None else "—"
        g_count = p.get("google_count") if p.get("google_count") is not None else "—"
        flag_html = ' <span style="color:#b45309;font-weight:700">⚠</span>' if flagged else ""
        snap_rows.append(
            f'<tr><td style="white-space:nowrap">{_e(_fmt_date(p.get("generated_at")))}</td>'
            f'<td style="text-align:center;font-weight:700;color:{_color(s)}">{s if s is not None else "—"}</td>'
            f'<td style="text-align:center">{delta_cell}</td>'
            f'{pillar_cells}'
            f'<td style="text-align:center">{g_rating}</td>'
            f'<td style="text-align:center">{g_count}</td>'
            f'<td style="font-size:8.5px;color:{_MUTE}">{_e(settings) or "—"}{flag_html}</td></tr>')
    pillar_ths = "".join('<th style="text-align:center">%s</th>' % _e(r["label"].split(" & ")[0]) for r in st["pillars"])

    moves_html = "".join(
        f'<li><b>{_e(_fmt_date(m["date"]))}</b> — score moved {_delta_html(m["delta"])} to {m["score"]}</li>'
        for m in st["moves"]) or f'<li style="color:{_MUTE}">No score changes recorded yet.</li>'
    drift_html = "".join(
        f'<li><b>{_e(_fmt_date(d["date"]))}</b> — run settings differed from the current configuration ({_e(", ".join(d["flags"]))}); treat that point with care.</li>'
        for d in st["drift"])

    limitations_html = _e(DATA_LIMITATIONS_BLOCK).replace("\n\n", "<br><br>")
    best = st["best_pillar"]; worst = st["worst_pillar"]
    summary_bits = [
        f'<div class="kpi"><div class="kpi-l">Latest Pulse Score</div>{latest_html}</div>',
        f'<div class="kpi"><div class="kpi-l">Since previous snapshot</div><div class="kpi-v">{_delta_html(st["delta_prev"])}</div></div>',
        f'<div class="kpi"><div class="kpi-l">Since tracking began</div><div class="kpi-v">{_delta_html(st["delta_first"])}</div></div>',
        f'<div class="kpi"><div class="kpi-l">Most improved pillar</div><div class="kpi-s">{_e(best["label"]) + " " + _delta_html(best["delta"]) if best else "—"}</div></div>',
        f'<div class="kpi"><div class="kpi-l">Largest decline</div><div class="kpi-s">{_e(worst["label"]) + " " + _delta_html(worst["delta"]) if worst else "—"}</div></div>',
    ]

    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
      * {{ box-sizing:border-box; margin:0; padding:0; }}
      body {{ font-family:'Inter','Helvetica Neue',Arial,sans-serif; color:{_INK}; font-size:11px; }}
      .band {{ background:{_TEAL}; color:#fff; padding:26px 40px 22px; }}
      .band .top {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:16px; }}
      .band .kick {{ font-size:10px; letter-spacing:.14em; text-transform:uppercase; color:#9FD8CF; margin-bottom:8px; }}
      .band h1 {{ font-size:24px; font-weight:700; margin:2px 0 4px; }}
      .band .sub {{ font-size:13px; color:#CFEAE6; }}
      .meta {{ display:flex; gap:26px; padding:12px 40px; background:{_PALE}; font-size:10.5px; color:{_MUTE}; border-bottom:1px solid #d7e7e2; }}
      .meta b {{ color:{_TEAL}; font-size:13px; display:block; }}
      h2 {{ font-size:13px; text-transform:uppercase; letter-spacing:.08em; color:{_TEAL}; margin:22px 40px 10px; padding-bottom:6px; border-bottom:2px solid {_PALE}; }}
      .sec {{ padding:0 40px; }}
      .kpis {{ display:grid; grid-template-columns:1.3fr 1fr 1fr 1.2fr 1.2fr; gap:12px; }}
      .kpi {{ background:{_PALE}; border-radius:8px; padding:12px 14px; }}
      .kpi-l {{ font-size:9px; text-transform:uppercase; letter-spacing:.08em; color:{_MUTE}; margin-bottom:6px; }}
      .kpi-v {{ font-size:22px; font-weight:800; }}
      .kpi-s {{ font-size:11.5px; font-weight:600; line-height:1.35; }}
      .analyst {{ margin-top:14px; padding:12px 16px; border-left:3px solid {_TEAL2}; background:#fafcfb; font-size:11.5px; line-height:1.55; }}
      .chart {{ background:#fff; border:1px solid #e3ede9; border-radius:8px; padding:10px 12px; }}
      .smalls {{ display:grid; grid-template-columns:1fr 1fr; gap:12px; }}
      .small {{ border:1px solid #e3ede9; border-radius:8px; padding:8px 10px; }}
      .small-h {{ font-size:9.5px; font-weight:700; color:{_TEAL}; margin-bottom:4px; }}
      table.t {{ width:100%; border-collapse:collapse; font-size:10px; margin-top:8px; }}
      table.t th {{ background:{_TEAL}; color:#fff; font-weight:600; font-size:8.5px; text-transform:uppercase; letter-spacing:.04em; padding:6px 7px; text-align:left; }}
      table.t td {{ padding:6px 7px; border-bottom:1px solid #e6efec; vertical-align:middle; }}
      table.t tr:nth-child(even) td {{ background:#fafcfb; }}
      ul.notes {{ margin:6px 0 0 18px; line-height:1.6; }}
      .note {{ margin:14px 40px 24px; padding:12px 16px; background:{_PALE}; border-radius:8px; font-size:9.5px; color:{_MUTE}; line-height:1.55; }}
      .note b {{ color:{_TEAL}; }}
      .pb {{ break-before:page; page-break-before:always; }}
      .blk {{ break-inside:avoid; page-break-inside:avoid; }}
      h2 {{ break-after:avoid; page-break-after:avoid; }}
      table.t thead {{ display:table-header-group; }}
      table.t tr {{ break-inside:avoid; page-break-inside:avoid; }}
      .chart, .small, .kpi, .analyst, .note {{ break-inside:avoid; page-break-inside:avoid; }}
      table.snap td {{ padding:4.5px 7px; }}
    </style></head><body>
      <div class="band">
        <div class="top">{_logo_html()}<div style="text-align:right;font-size:10px;letter-spacing:.1em;color:#9FD8CF">AI REPUTATION<br>TREND REPORT</div></div>
        <div class="kick">{_e(PRODUCT_SUBTITLE)} &middot; Trend Report</div>
        <h1>{_e(entity.get("entity_name"))}</h1>
        <div class="sub">{sub}</div>
      </div>
      <div class="meta">
        <div><b>{st["n"]}</b>snapshots</div>
        <div><b>{_e(st["period"][0])}</b>first snapshot</div>
        <div><b>{_e(st["period"][1])}</b>latest snapshot</div>
        <div><b>{_e(scope)}</b>scope</div>
        <div><b>{_e(today)}</b>prepared{(" for " + _e(prepared_for)) if prepared_for else ""}</div>
      </div>

      <section class="blk">
      <h2>Executive summary</h2>
      <div class="sec">
        <div class="kpis">{"".join(summary_bits)}</div>
        {f'<div class="analyst">{analyst}</div>' if analyst else ''}
      </div>
      </section>

      <section class="blk">
      <h2>Pulse Score over time</h2>
      <div class="sec"><div class="chart">{_score_chart(points)}</div>
        <div style="font-size:9px;color:{_MUTE};margin-top:6px">Shaded bands are the national quartiles (1st: 75+, 2nd: 68–74, 3rd: 58–67, 4th: below 58).</div></div>
      </section>

      <section class="blk pb">
      <h2 style="margin-top:28px">Pillar trends</h2>
      <div class="sec">
        <div class="smalls">{smalls}</div>
        <table class="t"><thead><tr><th>Pillar</th><th style="text-align:center">First</th><th style="text-align:center">Latest</th><th style="text-align:center">Change</th></tr></thead>
        <tbody>{pillar_rows}</tbody></table>
        {f'<div style="font-size:9px;color:{_MUTE};margin-top:6px">Scored on the practice rubric.</div>' if st["practice_rubric"] else ''}
      </div>
      </section>

      <section class="blk">
      <h2>Google reputation over time</h2>
      <div class="sec"><div class="chart">{_google_chart(points)}</div>
        <div style="font-size:9px;color:{_MUTE};margin-top:6px">Solid line: Google rating (left axis). Dashed line: review count (right axis).</div></div>
      </section>

      <h2>Snapshots</h2>
      <div class="sec">
        <table class="t snap"><thead><tr><th>Date</th><th style="text-align:center">Score</th><th style="text-align:center">Δ</th>{pillar_ths}
          <th style="text-align:center">Google</th><th style="text-align:center">Reviews</th><th>Run settings</th></tr></thead>
        <tbody>{"".join(snap_rows)}</tbody></table>
      </div>

      <section class="blk">
      <h2>Notable changes</h2>
      <div class="sec"><ul class="notes">{moves_html}{drift_html}</ul></div>
      </section>

      <div class="note"><b>Methodology.</b> {_e(AIVS_DISCLAIMER)}<br><br>{limitations_html}</div>
    </body></html>"""


def render_trend_report_pdf(entity: dict, points: list[dict], pdf_path: str, *,
                            brand: str = "original", with_analyst: bool = True,
                            prepared_for: str = "") -> None:
    """Render the Trend Report PDF for one tracked entity."""
    _rebrand_for_display(entity)
    _rebrand_for_display(points)
    analyst = analyst_paragraph(entity, compute_stats(entity, points)) if with_analyst else ""
    html_str = build_trend_html(entity, points, analyst=analyst, prepared_for=prepared_for)
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
                '<div style="width:100%;font-family:Arial,Helvetica,sans-serif;font-size:8px;color:#8a9aaa;'
                'display:flex;justify-content:space-between;align-items:center;padding:0 40px 10px;box-sizing:border-box">'
                '<span style="letter-spacing:0.05em">Prepared by Pulse | RLDatix &nbsp;&mdash;&nbsp; Confidential</span>'
                '<span>Page <span class="pageNumber"></span> of <span class="totalPages"></span></span></div>'
            ),
        )
        browser.close()
