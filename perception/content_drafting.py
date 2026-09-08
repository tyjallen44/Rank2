"""Content drafting engine — Phase 3 remediation for the Content Analysis sandbox.

Turns verified findings into publication-ready draft content: schema.org JSON-LD,
llms.txt, robots.txt fixes, exact Wikidata edits, and Wikipedia talk-page
requests. Drafts write back into the findings' `draft_content` slot so Report 2
re-renders deterministically.

HARD GUARDRAILS (enforced in the prompt): drafts use ONLY facts explicitly
provided; anything unknown is a literal [VERIFY: ...] placeholder, never guessed;
no invented citations, ever. This mirrors Pulse's "never estimated" discipline —
critical because this content gets published.
"""
from __future__ import annotations

import json

import anthropic

client = anthropic.Anthropic()
_MODEL = "claude-opus-4-8"

_DRAFT_TOOL = {
    "name": "submit_drafts",
    "description": "Submit publication-ready draft content, one entry per finding.",
    "input_schema": {
        "type": "object",
        "properties": {
            "drafts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "finding_id": {"type": "string"},
                        "draft_content": {"type": "string",
                                          "description": "Ready-to-use content for this finding's remediation type. Plain text / code as appropriate."},
                    },
                    "required": ["finding_id", "draft_content"],
                },
            },
        },
        "required": ["drafts"],
    },
}

_SYSTEM = (
    "You are a healthcare digital-content specialist drafting publication-ready remediation "
    "content for a provider's AI-visibility gaps. Your drafts will be reviewed by a human and "
    "then published, so accuracy is paramount.\n\n"
    "ABSOLUTE RULES:\n"
    "1. Use ONLY the facts explicitly provided in FACTS. Never invent names, addresses, phone "
    "numbers, NPIs, credentials, awards, dates, or statistics.\n"
    "2. For any value you need but were not given, insert a literal placeholder like "
    "[VERIFY: physician NPI] — never guess.\n"
    "3. NEVER fabricate citations or references of any kind. If a citation is needed (e.g. a "
    "Wikipedia edit), write [VERIFY: cite a published source] instead.\n"
    "4. Draft per the finding's remediation type:\n"
    "   - schema_markup: a complete, valid JSON-LD <script type=\"application/ld+json\"> block "
    "(MedicalOrganization, and Physician if a practice), populated from FACTS with placeholders "
    "for the rest.\n"
    "   - website_fix (llms.txt): a ready-to-publish llms.txt file. (robots.txt AI-blocking): "
    "the exact robots.txt lines to allow reputable AI crawlers. (thin content): specific copy/"
    "structure guidance.\n"
    "   - wikidata_edit: the exact property→value statements to add (e.g. 'P856 (official "
    "website) = <url>'), one per line, using provided values and [VERIFY: ...] otherwise.\n"
    "   - talk_page_request: a polite, well-formatted Wikipedia Talk-page edit request that "
    "states what is outdated/missing and the correct value, includes a short note that direct "
    "editing is avoided due to conflict-of-interest guidelines, and uses [VERIFY: cite a "
    "published source] wherever a citation would be required.\n"
    "   - reputation_program: a concrete review-generation plan. The review requests are delivered "
    "as HIPAA-compliant MMS (multimedia text messages) that include a photo of the treating "
    "physician who provided the care — this is how RLDatix Reputation Management sends them, and the "
    "physician image materially lifts response rates. Draft the MMS request message template(s) "
    "accordingly (reference the physician by name/photo with a [VERIFY: physician name] / [attach: "
    "treating physician photo] placeholder), plus cadence, the platform to direct patients to, and a "
    "short review-response template. Never fabricate review text or patient quotes. IF THE FINDING "
    "COVERS MULTIPLE LOCATIONS (its text says so), write ONE reusable template that applies to all of "
    "them — use [FACILITY] and [CITY] placeholders instead of naming any single location, and do NOT "
    "cite a specific rating or review count (those live in the per-location table).\n"
    "   - listing_management: the exact Google Business Profile fields to standardize/claim "
    "(name, address, phone, categories, hours) using FACTS, with [VERIFY: ...] for anything "
    "unknown, and the steps to consolidate duplicate/unclaimed listings.\n"
    "   - leapfrog_submission: a concrete action plan to participate in (or improve standing in) "
    "the Leapfrog Hospital Survey — register/claim the hospital(s) at leapfroggroup.org, the survey "
    "sections to complete, the submission window/deadlines to confirm ([VERIFY: current cycle dates]), "
    "who owns it internally (quality/patient-safety leadership), and how the published grade then "
    "feeds patient- and AI-facing safety signals. This is an operational plan, not website copy.\n"
    "   - quality_improvement: a short action plan to verify and improve the CMS Care Compare "
    "profile (confirm data completeness/accuracy at medicare.gov/care-compare, the measures driving "
    "the star rating, and internal ownership). Operational plan, not website copy.\n"
    "Keep each draft self-contained and immediately usable. Return one entry per finding via "
    "submit_drafts."
)


# Draft in small batches so a large report (e.g. a hospital network with a
# finding per facility) never exhausts a single response's token budget and
# truncates mid-JSON — the failure mode that silently produced empty drafts.
_BATCH_SIZE = 4
_MAX_TOKENS = 16000


def _draft_batch(facts_lines: list, batch: list) -> tuple[dict, bool]:
    """One API call for a small batch. Returns ({finding_id: draft_content},
    truncated) where `truncated` is True if the response hit the token cap."""
    find_lines = [
        f"[{f.get('finding_id')}] platform={f.get('platform')} "
        f"remediation_type={f.get('remediation_type')}\n"
        f"  issue: {f.get('teaser_summary')}\n"
        f"  current: {f.get('current_state')}\n  expected: {f.get('expected_state')}"
        for f in batch
    ]
    prompt = ("FACTS (the ONLY facts you may treat as true):\n" + "\n".join(facts_lines)
              + "\n\nFINDINGS TO DRAFT:\n" + "\n\n".join(find_lines)
              + "\n\nDraft publication-ready content for each finding. Remember: facts only, "
                "[VERIFY: ...] for anything unknown, no invented citations.")
    resp = client.messages.create(
        model=_MODEL, max_tokens=_MAX_TOKENS, tools=[_DRAFT_TOOL],
        tool_choice={"type": "tool", "name": "submit_drafts"},
        system=_SYSTEM, messages=[{"role": "user", "content": prompt}],
    )
    out = {}
    for block in resp.content:
        if block.type == "tool_use" and block.name == "submit_drafts":
            d = block.input if isinstance(block.input, dict) else json.loads(block.input)
            for item in d.get("drafts", []):
                fid = item.get("finding_id")
                dc = (item.get("draft_content") or "").strip()
                if fid and dc:
                    out[fid] = dc
    return out, (resp.stop_reason == "max_tokens")


def _draft_recursive(facts_lines: list, batch: list) -> dict:
    """Draft a batch; if the response still truncates, split in half and retry
    each side until it fits (or a single finding can't be split further)."""
    try:
        out, truncated = _draft_batch(facts_lines, batch)
    except Exception:
        return {}
    if truncated and len(batch) > 1:
        mid = len(batch) // 2
        merged = {}
        merged.update(_draft_recursive(facts_lines, batch[:mid]))
        merged.update(_draft_recursive(facts_lines, batch[mid:]))
        return merged
    return out


def draft_findings(entity_name: str, location: str, entity_kind: str,
                   facts: dict, findings: list) -> dict:
    """Return {finding_id: draft_content} for the given findings. Never raises —
    a failed batch contributes nothing rather than aborting the whole run.
    Findings are drafted in batches of _BATCH_SIZE so large reports don't
    truncate; see _draft_recursive for the split-on-truncation safety net."""
    draftable = [f for f in findings if (f.get("remediation_type") or "")
                 and f.get("status") != "not_assessed"]
    if not draftable:
        return {}

    facts_lines = [f"- Entity name: {entity_name}", f"- Location: {location}",
                   f"- Type: {'specialty practice' if entity_kind == 'practice' else 'hospital / health system'}"]
    for k, label in [("website_urls", "Website URL(s)"), ("specialty", "Specialty"),
                     ("wikidata_qid", "Wikidata QID"), ("wikipedia_article", "Wikipedia article")]:
        v = facts.get(k)
        if v:
            facts_lines.append(f"- {label}: {v if not isinstance(v, list) else ', '.join(v)}")

    out = {}
    for i in range(0, len(draftable), _BATCH_SIZE):
        out.update(_draft_recursive(facts_lines, draftable[i:i + _BATCH_SIZE]))
    return out
