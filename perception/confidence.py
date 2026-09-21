"""Score confidence — how much public evidence sits behind an AI Reputation score.

A score built from two Google reviews and no CMS record is not the same as one built
from 800 reviews across six confirmed locations. This is a small, deterministic read on
the evidence so users know how hard to lean on the number.

Levels:  high   — 200+ reviews behind the score and every expected public signal present
         medium — thinner evidence or one missing signal
         low    — under 50 reviews, no verified Google listing, or several gaps
"""
from typing import Optional


def _int(v) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def score_confidence(result) -> Optional[dict]:
    rankings = list(getattr(result, "rankings", None) or [])
    if not rankings:
        return None
    p = next((r for r in rankings if getattr(r, "is_target", False)), rankings[0])
    et = getattr(result, "entity_type", None) or "hospital"
    profile = getattr(result, "weighting_profile", None) or getattr(p, "weighting_profile", None) or ""
    is_practice = et in ("practice", "service_line") or str(profile).startswith("practice_")

    fd = p.google_footprint.front_door
    sa = p.google_footprint.system_aggregate
    google_rating = fd.rating
    reviews, locations = 0, 1
    rows = [r for r in (getattr(result, "practice_composite_rows", None) or []) if not r.get("not_established")]
    if rows:
        reviews = sum(_int(r.get("total_reviews")) for r in rows)
        locations = len(rows)
        if google_rating is None:
            google_rating = next((r.get("google_rating") for r in rows if r.get("google_rating")), None)
    elif sa.available and _int(sa.total_reviews):
        reviews, locations = _int(sa.total_reviews), max(1, _int(sa.location_count))
    else:
        reviews = _int(fd.count)

    unscored = [k for k, v in p.tier_scores.as_dict().items() if v is None]
    reasons, penalties = [], 0
    if google_rating is None and reviews == 0:
        reasons.append("no verified Google listing"); penalties += 2
    elif reviews < 50:
        reasons.append(f"only {reviews} review{'s' if reviews != 1 else ''}"); penalties += 1
    if not is_practice and et == "hospital" and getattr(p, "cms_star_rating", None) is None:
        reasons.append("no CMS star rating on record"); penalties += 1
    if unscored:
        reasons.append(f"{len(unscored)} pillar{'s' if len(unscored) != 1 else ''} unscored"); penalties += 1
    if getattr(result, "web_search_used", None) is False:
        reasons.append("no live web search (written from model knowledge)"); penalties += 2

    if penalties == 0 and reviews >= 200:
        level = "high"
    elif penalties >= 2 or (google_rating is None and reviews == 0):
        level = "low"
    else:
        level = "medium"

    basis = f"{reviews:,} review{'s' if reviews != 1 else ''}"
    if locations > 1:
        basis += f" across {locations} locations"
    if reviews < 50:
        basis = ""                       # the "only N reviews" reason already says it
    note = "; ".join([x for x in [basis] + reasons if x])
    if level == "medium" and not reasons:
        note += " (200+ for high)"
    return {"level": level, "label": level.capitalize(), "note": note,
            "reviews": reviews, "locations": locations, "reasons": reasons}
