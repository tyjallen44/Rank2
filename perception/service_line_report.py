"""Phase D1 — aggregate the service-line scorecards into a system summary and
derive actionable CIK findings (see docs/service-line-listing-analysis.md).

Finding granularity (locked): invisible + reputation-drag are PER LINE; schema,
listing-completeness, and AI-invisibility gaps are AGGREGATED into single findings
so the report stays readable. Findings are returned as ContentFinding-shaped dicts
(platform "service_line") so they slot into the existing findings list and get
drafted remediation for free.
"""
from __future__ import annotations

from .models import ServiceLineScorecardSet, ServiceLineSummary

_REP_DRAG = 3.5          # avg rating below this → a reputation-drag finding
_MANY = 2               # aggregate a gap only when it affects at least this many lines


def _dim(card, key):
    return next((d for d in card.dimensions if d.key == key), None)


def build_summary(cards: ServiceLineScorecardSet, sampling_note: str = "") -> ServiceLineSummary:
    scs = cards.scorecards
    s = ServiceLineSummary(total_lines=len(scs), sampling_note=sampling_note)
    for c in scs:
        if c.management_status in ("managed", "partial", "unmanaged", "invisible"):
            setattr(s, c.management_status, getattr(s, c.management_status) + 1)
    scored = [c for c in scs if c.overall_score is not None and c.management_status != "invisible"]
    if scored:
        s.avg_overall = round(sum(c.overall_score for c in scored) / len(scored))
        ranked = sorted(scored, key=lambda c: c.overall_score)
        s.worst = ranked[0].canonical_label
        s.best = ranked[-1].canonical_label

    # Cross-cutting one-liners (only when they affect >= _MANY lines).
    no_schema = [c.canonical_label for c in scs
                 if (_dim(c, "content") or _blank()).meta.get("has_schema") is False]
    if len(no_schema) >= _MANY:
        s.cross_cutting.append(f"{len(no_schema)} service pages lack schema.org medical markup.")
    missing_desc = [c.canonical_label for c in scs
                    if "description" in (_dim(c, "completeness") or _blank()).meta.get("missing", [])]
    if len(missing_desc) >= _MANY:
        s.cross_cutting.append(f"{len(missing_desc)} service lines' Google listings have no description.")
    ai_no = [c.canonical_label for c in scs
             if (_dim(c, "findability") or _blank()).meta.get("ai_surfaced") is False]
    if len(ai_no) >= _MANY:
        s.cross_cutting.append(f"{len(ai_no)} service lines don't surface in AI answers for their specialty.")
    return s


class _blank:
    meta: dict = {}


def _finding(fid_platform, severity, teaser, current, expected, remediation, evidence=None):
    return dict(platform="service_line", category="opportunity", severity=severity,
                status="verified", teaser_summary=teaser, current_state=current,
                expected_state=expected, remediation_type=remediation,
                evidence=evidence or [])


def derive_findings(cards: ServiceLineScorecardSet) -> list:
    """ContentFinding-shaped dicts (no ids — the caller re-numbers CIK ids)."""
    out = []
    scs = cards.scorecards

    # 1. Invisible lines — PER LINE, high.
    for c in scs:
        if c.management_status == "invisible":
            out.append(_finding(
                "service_line", "high",
                f"{c.canonical_label} is invisible — no findable listing for patients or AI.",
                f"No affiliated Google listing (and no clear dedicated page) was found for {c.canonical_label}.",
                "A claimed Google Business Profile and a dedicated service-line page so patients and AI assistants can find it.",
                "listing_management",
                [s for s in c.notes]))

    # 2. Reputation drag — PER LINE.
    for c in scs:
        rep = _dim(c, "reputation")
        avg = rep.meta.get("avg_rating") if rep else None
        if avg is not None and avg < _REP_DRAG:
            out.append(_finding(
                "service_line", "high" if avg < 3.0 else "medium",
                f"{c.canonical_label} has a weak Google reputation ({avg}★) — a drag on patient attraction.",
                f"{c.canonical_label} listings average {avg}★ across {rep.meta.get('listings', 0)} location(s).",
                "4.5★+ with steady, recent review volume.",
                "reputation_program"))

    # 3. Schema gap — AGGREGATED.
    no_schema = [c.canonical_label for c in scs
                 if (_dim(c, "content") or _blank()).meta.get("has_schema") is False]
    if len(no_schema) >= _MANY:
        out.append(_finding(
            "service_line", "medium",
            f"{len(no_schema)} service-line pages lack schema.org markup — AI can't reliably parse them.",
            f"No MedicalOrganization/MedicalClinic schema on: {', '.join(no_schema[:12])}"
            + ("…" if len(no_schema) > 12 else "") + ".",
            "MedicalOrganization/MedicalClinic (and MedicalProcedure) schema on each service-line page.",
            "schema_markup"))

    # 4. Listing-completeness gap — AGGREGATED (most common missing field).
    missing_desc = [c.canonical_label for c in scs
                    if "description" in (_dim(c, "completeness") or _blank()).meta.get("missing", [])]
    if len(missing_desc) >= _MANY:
        out.append(_finding(
            "service_line", "low",
            f"{len(missing_desc)} service lines' Google listings are missing a business description.",
            f"No GBP description on: {', '.join(missing_desc[:12])}" + ("…" if len(missing_desc) > 12 else "") + ".",
            "A complete, keyword-relevant description on each service line's Google Business Profile.",
            "listing_management"))

    # 5. AI-invisibility — AGGREGATED.
    ai_no = [c.canonical_label for c in scs
             if (_dim(c, "findability") or _blank()).meta.get("ai_surfaced") is False]
    if len(ai_no) >= _MANY:
        out.append(_finding(
            "service_line", "medium",
            f"{len(ai_no)} service lines don't surface when patients ask AI assistants for care.",
            f"The system was not named for 'best [specialty] in [metro]' for: {', '.join(ai_no[:12])}"
            + ("…" if len(ai_no) > 12 else "") + ".",
            "The system is named among the top options when AI assistants are asked for these specialties.",
            "listing_management"))

    return out
