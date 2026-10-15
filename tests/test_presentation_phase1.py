"""Phase 1 presentation fixes (report-quality cleanup): 1.3 anchor row, 1.4 knowledge-base match,
1.5 website-cited sentence, 1.6 spot-check coverage, 1.8 canonical review totals."""
from types import SimpleNamespace

from perception.content_analyzer import _name_matches
from perception.pdf import website_cited_sentence, spotcheck_incomplete
from perception.review_totals import compute, google_phrase


# 1.4
def test_wikipedia_match_requires_whole_distinctive_tokens():
    assert not _name_matches("Georgia Carolina Orthopedics", "University of Georgia")
    assert not _name_matches("Summit Medical Group", "Summit County, Ohio")
    assert not _name_matches("Ibji Doctors' Office - Chicago-Avondale", "Avondale, Chicago")
    assert _name_matches("Illinois Bone and Joint Institute", "Illinois Bone & Joint Institute")
    assert _name_matches("OrthoSouth", "OrthoSouth")
    assert _name_matches("Campbell Clinic", "Campbell Clinic")                  # only-generic brand: all tokens present
    assert not _name_matches("Campbell Clinic", "Campbell Soup Company")


# 1.5
def _sc(cited_answers, answers, cited_by, our="orthosouth.org"):
    return {"our_domain": our, "asked": answers, "our_domain_cited": cited_answers > 0,
            "sourcing": {"cited_answers": cited_answers, "answers": answers, "cited_by": cited_by}}


def test_cited_sentence_both_branches():
    s = website_cited_sentence(_sc(0, 144, []))
    assert "not cited</span> by any assistant." in s and "cited in" not in s
    s = website_cited_sentence(_sc(12, 144, ["ChatGPT", "Claude"]))
    assert "cited</span> in 12 of 144 answers (by ChatGPT, Claude)." in s
    assert "by any assistant" not in s


# 1.6
def test_incomplete_when_an_assistant_mostly_failed():
    sc = {"per_assistant": [{"assistant": "Claude", "asked": 60, "errors": 0}, {"assistant": "Gemini", "asked": 0, "errors": 60}]}
    assert spotcheck_incomplete(sc)
    sc = {"per_assistant": [{"assistant": "Claude", "asked": 58, "errors": 2}]}
    assert not spotcheck_incomplete(sc)


# 1.8
def _result(profiles=None, rows=None):
    fd = SimpleNamespace(verified=True, count=758, rating=4.3)
    sa = SimpleNamespace(available=False, total_reviews=None, location_count=None)
    p = SimpleNamespace(google_footprint=SimpleNamespace(front_door=fd, system_aggregate=sa))
    return SimpleNamespace(profile_audit={"profiles": profiles or []}, practice_composite_rows=rows or [], rankings=[p])


def test_review_totals_come_from_verified_profiles_not_anchor_times_locations():
    profiles = [{"found": True, "is_anchor": True, "review_count": 758}] + [{"found": True, "review_count": 791} for _ in range(9)]
    rows = [{"is_anchor": True, "total_reviews": 758, "google_count": 758}] + [{"total_reviews": 791, "google_count": 791} for _ in range(9)]
    t = compute(_result(profiles, rows))
    assert t["google_total"] == 758 + 9 * 791 and t["google_locations"] == 10 and t["anchor_google"] == 758
    assert google_phrase(t) == "7,877 Google reviews across 10 profiles (analyzed listing: 758)"


def test_review_totals_fall_back_to_front_door():
    t = compute(_result())
    assert t == {**t, "google_total": 758, "google_locations": 1, "source": "front_door"}
    assert google_phrase(t) == "758 Google reviews"
