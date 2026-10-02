"""AI Readiness Checklist + pillar blurbs."""
from datetime import date
from perception.models import AnalysisResult, RankedProvider, ContentFindings, ContentFinding, GoogleFootprint, GoogleFrontDoor
from perception.checklist import build_checklist, summarize, pillar_blurbs, CHECKS


def _practice(**kw):
    p = RankedProvider(rank=1, name="Ortho Group", weighting_profile="practice_procedural", report_type="practice",
                       google_footprint=GoogleFootprint(front_door=GoogleFrontDoor(query="x", verified=True, rating=4.7, count=258)))
    base = dict(run_id="r", location="Memphis, TN", generated_at=date.today(), individual_report=True, entity_name="Ortho Group",
                entity_type="practice", weighting_profile="practice_procedural", rankings=[p])
    base.update(kw)
    return AnalysisResult(**base)


def test_always_ten_rows_in_fixed_order():
    rows = build_checklist(_practice())
    assert [r["id"] for r in rows] == [c[0] for c in CHECKS]
    assert all(r["status"] == "na" for r in rows if r["id"] not in ("review_volume",))
    assert summarize(rows)["na"] == 9


def test_measured_site_and_profiles_and_record():
    wf = {"status": "measured", "pages": 6, "robots_allows_ai": False, "org_schema": True, "physician_schema": False,
          "physician_pages": 0, "sitemap": True, "llms_txt": False}
    au = {"summary": {"checked": 4, "linked": 2, "with_hours": 4, "with_phone": 4, "with_photos": 3}}
    f = ContentFindings(run_id="r", source_snapshot={"wikidata_qid": "Q1", "wikipedia_article": None},
                        findings=[ContentFinding(finding_id="CIK-001", platform="website", category="missing", severity="high",
                                                 teaser_summary="Your robots.txt blocks AI crawlers"),
                                  ContentFinding(finding_id="CIK-004", platform="wikipedia", category="missing", severity="low",
                                                 teaser_summary="No Wikipedia article")])
    rows = {r["id"]: r for r in build_checklist(_practice(website_facts=wf, profile_audit=au), f)}
    assert rows["ai_access"]["status"] == "pass"
    assert rows["robots"]["status"] == "fail" and rows["robots"]["finding"] == "CIK-001"
    assert rows["org_schema"]["status"] == "pass" and rows["physician_schema"]["status"] == "fail"
    assert rows["bio_pages"]["status"] == "fail" and rows["sitemap"]["status"] == "pass" and rows["llms_txt"]["status"] == "fail"
    assert rows["google_profiles"]["status"] == "partial"
    assert rows["review_volume"]["status"] == "pass" and "258" in rows["review_volume"]["detail"]
    assert rows["public_record"]["status"] == "partial" and rows["public_record"]["finding"] == "CIK-004"
    assert rows["robots"]["feeds"] == "Identity & Machine-Readability"
    assert rows["google_profiles"]["feeds"] == "Reviews & Reputation"


def test_blocked_site_and_hospital_feeds():
    wf = {"status": "blocked", "crawler_probe": {"blocked": ["GPTBot", "ClaudeBot"]}}
    r = _practice(website_facts=wf, entity_type="hospital", weighting_profile="procedural")
    r.rankings[0].weighting_profile = "procedural"; r.rankings[0].report_type = "hospital"
    rows = {x["id"]: x for x in build_checklist(r)}
    assert rows["ai_access"]["status"] == "fail" and "GPTBot" in rows["ai_access"]["detail"]
    assert rows["robots"]["status"] == "na"
    assert rows["physician_schema"]["status"] == "na" and rows["bio_pages"]["status"] == "na"
    assert rows["ai_access"]["feeds"] == "Content visibility (not scored)"
    assert rows["review_volume"]["feeds"] == "Experience & Reviews"


def test_pillar_blurbs_follow_the_rubric():
    labels = [l for l, _ in pillar_blurbs(_practice())]
    assert labels == ["Practitioner Credentials & Clinical Quality", "Reviews & Reputation", "Identity & Machine-Readability", "Access & Fit"]
    assert all(b for _, b in pillar_blurbs(_practice()))
    h = _practice(entity_type="hospital", weighting_profile="procedural"); h.rankings[0].weighting_profile = "procedural"
    assert [l for l, _ in pillar_blurbs(h)][0] == "Outcomes & Safety"
