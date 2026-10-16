"""Cleanup Phase 4 — consistency and association readiness (4.2–4.6). Offline."""
import json, pathlib
import pytest


# 4.5 ─────────────────────────────────────────────────────────────────────────
def test_rank_in_cohort_replaces_own_score_and_keeps_n_fixed():
    from perception.groups import rank_in_cohort
    cohort = {"n": 4, "median": 70, "members": [
        {"entity_name": "A", "location": "X, TN", "score": 80}, {"entity_name": "B", "location": "X, TN", "score": 70},
        {"entity_name": "C", "location": "X, TN", "score": 60}, {"entity_name": "D", "location": "X, TN", "score": 50}]}
    assert rank_in_cohort(cohort, "C", "X, TN", 60) == 3
    assert rank_in_cohort(cohort, "C", "X, TN", 85) == 1         # a re-run moves the member, not the cohort
    assert rank_in_cohort(cohort, "c", "x, tn", 65) == 3         # case-insensitive key
    assert rank_in_cohort(cohort, "New Org", "Y, TN", 75) == 2   # not in the snapshot: placed among the others
    assert rank_in_cohort(cohort, "A", "X, TN", None) is None


def test_cover_line_prints_cohort_date_and_respects_the_peer_rank_switch():
    from types import SimpleNamespace
    from perception.deep_pdf import _group_cover_line, _group_summary_bullet
    r = SimpleNamespace(group_context={"group_name": "The OrthoForum", "ready": True, "rank": 9, "total": 24, "median": 66,
                                       "frozen": True, "cohort_date": "2026-10-01", "show_rank": True, "prospect": False},
                        rankings=[SimpleNamespace(ai_visibility_score=74)])
    html = _group_cover_line(r)
    assert "9th of 24" in html and "group median 66" in html and "cohort as of 2026-10-01" in html
    assert "cohort frozen 2026-10-01" in _group_summary_bullet(r)
    r.group_context["show_rank"] = False
    html = _group_cover_line(r)
    assert "The OrthoForum" in html and "9th of 24" not in html
    assert "Ranks" not in (_group_summary_bullet(r) or "")


# 4.6 ─────────────────────────────────────────────────────────────────────────
def test_attribution_cover_footer_and_path_forward():
    from types import SimpleNamespace
    from perception.deep_pdf import _attribution_cover_line, path_forward_section
    from perception.pdf import _report_footer, _attribution_of
    a = {"enabled": True, "association": "The OrthoForum", "team": "RLDatix Patient Experience & Growth", "email": "pxgrowth@rldatix.com",
         "cover_line": True, "logo_url": "", "tracked_url": "https://www.rldatix.com/pulse?utm_campaign=orthoforum"}
    r = SimpleNamespace(group_context={"group_name": "The OrthoForum", "attribution": a})
    assert "Provided to <strong>The OrthoForum</strong> members by the RLDatix Patient Experience &amp; Growth team" in _attribution_cover_line(r)
    foot = _report_footer("Data 10/16/2026", _attribution_of(r))
    assert "Prepared by PulseAI | RLDatix Patient Experience &amp; Growth" in foot and "pxgrowth@rldatix.com" in foot and "Confidential" in foot
    assert "Patient Experience" not in _report_footer("Data 10/16/2026", None)
    pf = path_forward_section(r)
    assert "Your Path Forward" in pf and "glad to help" in pf and "utm_campaign=orthoforum" in pf
    assert 'src="data:image/svg+xml;base64,' in pf                 # QR code present
    a["cover_line"] = False
    assert _attribution_cover_line(r) == ""
    assert path_forward_section(SimpleNamespace(group_context={})) == ""


def test_methodology_box_offers_the_team_when_attributed():
    from perception.pdf import _methodology_box_html
    assert "glad to help" in _methodology_box_html(["a", "b", "c", "d"], attribution={"team": "RLDatix Patient Experience & Growth"})
    assert "glad to help" not in _methodology_box_html(["a", "b", "c", "d"])


def test_qr_data_uri():
    from perception.qr import qr_svg_data_uri
    assert qr_svg_data_uri("https://example.com/x").startswith("data:image/svg+xml;base64,")
    assert qr_svg_data_uri("") == ""


# 4.2 ─────────────────────────────────────────────────────────────────────────
def test_location_reconciliation_line_counts_agree():
    from perception.deep_pdf import location_reconciliation_line
    rows = [{"sources": ["website", "google"]}, {"sources": ["website"]}, {"sources": ["google"]}, {"sources": ["google"], "composite": {"google_count": 5}}]
    assert location_reconciliation_line(rows) == "4 locations confirmed for this practice (2 on the website, 3 with a Google profile, 1 both)."
    assert location_reconciliation_line([{}], "hospital") == "1 location confirmed for this organization."


# 4.3 ─────────────────────────────────────────────────────────────────────────
def test_display_lists_never_repeat_a_name():
    from perception.listing_quality import _names, issues
    assert _names(["Georgia Carolina Orthopedics – Aiken", "georgia carolina orthopedics – aiken", "GCO Thomson"]) == "Georgia Carolina Orthopedics – Aiken, GCO Thomson"
    out = issues({"category": {"generic": ["A", "A", "B"], "counts": {"doctor": 3}, "consistent": True}})
    assert out == ["2 offices categorized generically on Google (A, B) rather than by specialty."]


# 4.4 ─────────────────────────────────────────────────────────────────────────
def test_competitor_variants_merge_with_combined_counts():
    from perception.spotcheck import merge_competitors
    out = merge_competitors([{"name": "Campbell Clinic Orthopaedics", "count": 56}, {"name": "Campbell Clinic", "count": 33},
                             {"name": "Piedmont Augusta", "count": 12}, {"name": "Piedmont Augusta Hospital", "count": 9},
                             {"name": "OrthoSouth", "count": 40}])
    names = {o["name"]: o for o in out}
    assert names["Campbell Clinic Orthopaedics"]["count"] == 89 and names["Campbell Clinic Orthopaedics"]["variants"] == ["Campbell Clinic"]
    assert names["Piedmont Augusta"]["count"] == 21
    assert names["OrthoSouth"]["count"] == 40 and len(out) == 3
    assert [o["name"] for o in out] == ["Campbell Clinic Orthopaedics", "OrthoSouth", "Piedmont Augusta"]
