from perception.data import website_facts as wf


def test_band_points_full_and_empty():
    f = {"status": "measured", "org_schema": True, "physician_schema": True, "physician_pages": 3, "sitemap": True, "llms_txt": True, "robots_allows_ai": True}
    f["breakdown"] = {k: (wf.BAND[k] if (f[k] if k != "physician_pages" else f[k] > 0) else 0) for k in wf.BAND}
    assert sum(f["breakdown"].values()) == 20
    assert sum(wf.BAND.values()) == 20


def test_override_arithmetic():
    assert wf.apply_identity_override(58, 12, 4) == 50          # model assumed 12, measured 4
    assert wf.apply_identity_override(58, None, 20) == 68       # unstated → default 10 assumed
    assert wf.apply_identity_override(5, 20, 0) == 0            # clamps at 0
    assert wf.apply_identity_override(None, 10, 10) is None     # unscored pillar untouched


def test_claims_scan():
    c = wf.scan_claims("USA Health University Hospital earned a Leapfrog Hospital Safety Grade of “C” in Spring 2026. Rated 2-star by CMS Care Compare. Magnet designation. Joint Commission Gold Seal.")
    assert c["leapfrog"] == "C" and c["leapfrog_mentioned"] and c["cms_stars"] == 2 and c["magnet"] and c["joint_commission"]
    c2 = wf.scan_claims("We participate in the Leapfrog Hospital Survey every year.")
    assert c2["leapfrog"] is None and c2["leapfrog_mentioned"]
    assert wf.scan_claims("Nothing here")["leapfrog"] is None


def test_claim_mismatch_and_summary():
    f = {"status": "measured", "points": 14, "claims": {"leapfrog": "A", "cms_stars": 4}}
    assert "Leapfrog grade A" in wf.claim_mismatch(f, {"leapfrog_grade": "C", "cms_star": 4, "leapfrog_cycle": "Spring 2026"})
    assert wf.claim_mismatch(f, {"leapfrog_grade": "A", "cms_star": 4}) is None
    assert wf.summary(f, "practice") == "website facts verified by crawl (14/20)"
    assert wf.summary({"status": "blocked"}, "practice") == "website blocks AI crawlers (verified)"
    assert wf.summary({"status": "skipped"}) is None
