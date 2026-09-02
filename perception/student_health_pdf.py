"""Branded PDF for a Student Health Clinics AI-visibility ranking.

Input is the stored result dict:
  {group_label, mode, rows: [{rank, school, clinic_name, city, state, url,
                              pulse_score, quartile, band_label, tiers, ai_says}]}
Rendered via Playwright (same harness as the other reports), RLDatix teal brand.
"""
from __future__ import annotations

import base64
import html
from datetime import date
from pathlib import Path

_TEAL = "#0F4146"
_TEAL2 = "#177B6E"
_PALE = "#EEF7F1"
_INK = "#2B3A3D"
_MUTE = "#5A6E72"

# Pillar slot → column label, in display order.
_PILLARS = [
    ("credentials_recognition",    "Findability & Identity"),
    ("clinical_outcomes_safety",   "Services & Access"),
    ("patient_experience_reviews", "Reviews & Reputation"),
    ("access_fit",                 "Machine-Readability"),
]

_QUARTILE = {"Q1": "1st Quartile", "Q2": "2nd Quartile",
             "Q3": "3rd Quartile", "Q4": "4th Quartile"}


def _e(s) -> str:
    return html.escape(str(s if s is not None else ""))


def _bar_color(v) -> str:
    if not isinstance(v, (int, float)):
        return "#d94f4f"
    if v < 58:
        return "#d94f4f"
    if v < 75:
        return "#e09b2a"
    return "#2e9e5b"


def _logo_html() -> str:
    p = Path(__file__).parent / "assets" / "logo-white.svg"
    if p.exists():
        data = base64.b64encode(p.read_bytes()).decode()
        return f'<img src="data:image/svg+xml;base64,{data}" style="height:34px" alt="RLDatix">'
    return '<div style="color:#fff;font-weight:700;font-size:22px">Pulse</div>'


def render_student_health_pdf(result: dict, pdf_path: str) -> None:
    from playwright.sync_api import sync_playwright
    html_str = _build_html(result)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content(html_str, wait_until="networkidle")
        page.pdf(
            path=str(pdf_path),
            format="A4",
            margin={"top": "0", "bottom": "0.6in", "left": "0", "right": "0"},
            print_background=True,
            display_header_footer=True,
            header_template="<span></span>",
            footer_template=(
                '<div style="width:100%;font-family:Arial,Helvetica,sans-serif;'
                'font-size:8px;color:#8a9aaa;display:flex;justify-content:space-between;'
                'align-items:center;padding:0 40px 10px;box-sizing:border-box">'
                '<span style="letter-spacing:0.05em">Prepared by Pulse | RLDatix &nbsp;&mdash;&nbsp; Confidential</span>'
                '<span>Page <span class="pageNumber"></span> of <span class="totalPages"></span></span>'
                '</div>'
            ),
        )
        browser.close()


def _build_html(result: dict) -> str:
    rows = result.get("rows", []) or []
    group = result.get("group_label") or "Student Health Clinics"
    scored = sum(1 for r in rows if r.get("pulse_score") is not None)
    today = date.today().strftime("%B %-d, %Y") if hasattr(date.today(), "strftime") else str(date.today())

    def _row(r: dict) -> str:
        t = r.get("tiers") or {}
        score = r.get("pulse_score")
        score_cell = (f'<span style="font-size:15px;font-weight:700;color:{_bar_color(score)}">{score}</span>'
                      if score is not None else '<span style="color:#c0392b">Failed</span>')
        q = _QUARTILE.get(r.get("quartile"), r.get("quartile") or "—")
        pills = "".join(
            f'<td style="text-align:center;font-weight:600;color:{_bar_color(t.get(k))}">'
            f'{t.get(k) if isinstance(t.get(k), (int, float)) else "—"}</td>'
            for k, _lbl in _PILLARS
        )
        loc = _e(r.get("city") or "")
        if r.get("state"):
            loc = (loc + ", " + _e(r["state"])) if loc else _e(r["state"])
        return (
            f'<tr>'
            f'<td style="text-align:center;font-weight:700;color:{_TEAL}">{_e(r.get("rank"))}</td>'
            f'<td><div style="font-weight:600;color:{_INK}">{_e(r.get("clinic_name"))}</div>'
            f'<div style="font-size:9px;color:{_MUTE}">{_e(r.get("school"))}</div></td>'
            f'<td style="font-size:10px;color:{_MUTE}">{loc}</td>'
            f'<td style="text-align:center">{score_cell}</td>'
            f'<td style="text-align:center;font-size:10px;color:{_MUTE}">{_e(q)}</td>'
            f'{pills}</tr>'
        )

    body_rows = "".join(_row(r) for r in rows)
    pillar_ths = "".join(f'<th style="text-align:center">{_e(lbl)}</th>' for _k, lbl in _PILLARS)

    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
      * {{ box-sizing:border-box; margin:0; padding:0; }}
      body {{ font-family:'Inter','Helvetica Neue',Arial,sans-serif; color:{_INK}; }}
      .band {{ background:{_TEAL}; color:#fff; padding:26px 40px; }}
      .band .kick {{ font-size:10px; letter-spacing:.14em; text-transform:uppercase; color:#9FD8CF; margin-bottom:8px; }}
      .band h1 {{ font-size:24px; font-weight:700; margin:2px 0 4px; }}
      .band .sub {{ font-size:13px; color:#CFEAE6; }}
      .band .top {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:14px; }}
      .meta {{ display:flex; gap:22px; padding:14px 40px; background:{_PALE};
               font-size:11px; color:{_MUTE}; border-bottom:1px solid #d7e7e2; }}
      .meta b {{ color:{_TEAL}; font-size:14px; }}
      .intro {{ padding:16px 40px 4px; font-size:11.5px; color:{_INK}; line-height:1.5; }}
      table.rank {{ width:100%; border-collapse:collapse; margin:8px 40px 0; width:calc(100% - 80px); font-size:11px; }}
      table.rank th {{ background:{_TEAL}; color:#fff; font-weight:600; font-size:9.5px;
                       text-transform:uppercase; letter-spacing:.04em; padding:8px 8px; text-align:left; }}
      table.rank td {{ padding:8px; border-bottom:1px solid #e6efec; vertical-align:middle; }}
      table.rank tr:nth-child(even) td {{ background:#fafcfb; }}
      .note {{ margin:16px 40px; padding:12px 16px; background:{_PALE}; border-radius:8px;
               font-size:10px; color:{_MUTE}; line-height:1.55; }}
      .note b {{ color:{_TEAL}; }}
    </style></head><body>
      <div class="band">
        <div class="top">{_logo_html()}<div style="text-align:right;font-size:10px;letter-spacing:.1em;color:#9FD8CF">STUDENT HEALTH<br>AI VISIBILITY RANKING</div></div>
        <div class="kick">Competitors Rankings &middot; Student Health</div>
        <h1>{_e(group)}</h1>
        <div class="sub">On-campus student health clinics, ranked by AI Visibility</div>
      </div>
      <div class="meta">
        <div><b>{len(rows)}</b><br>clinics</div>
        <div><b>{scored}</b><br>scored</div>
        <div><b>{_e(today)}</b><br>generated</div>
      </div>
      <div class="intro">Each clinic is scored 0&ndash;100 on how visibly and favorably AI assistants
        (ChatGPT, Gemini, Claude) present it when students ask about campus healthcare, then ranked.</div>
      <table class="rank">
        <thead><tr><th style="text-align:center">#</th><th>Clinic</th><th>Location</th>
          <th style="text-align:center">Pulse</th><th style="text-align:center">Quartile</th>{pillar_ths}</tr></thead>
        <tbody>{body_rows}</tbody>
      </table>
      <div class="note"><b>Rubric &mdash; Student Health.</b> The Pulse Score is a weighted blend of four
        pillars tuned to on-campus clinics: <b>Reviews &amp; Reputation</b> (30%), <b>Findability &amp;
        Identity</b> (25%), <b>Services &amp; Access</b> (25%), and <b>Machine-Readability &amp; Digital
        Presence</b> (20%). National quartiles: Q1 &ge;75 &middot; Q2 68&ndash;74 &middot; Q3 58&ndash;67 &middot; Q4 &lt;58.</div>
    </body></html>"""
