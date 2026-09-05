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
    "   - reputation_program: a concrete review-generation plan for the specific location. The "
    "review requests are delivered as HIPAA-compliant MMS (multimedia text messages) that include "
    "a photo of the treating physician who provided the care — this is how RLDatix Reputation "
    "Management sends them, and the physician image materially lifts response rates. Draft the MMS "
    "request message template(s) accordingly (reference the physician by name/photo with a "
    "[VERIFY: physician name] / [attach: treating physician photo] placeholder), plus cadence, the "
    "platform to direct patients to, and a short review-response template. Never fabricate review "
    "text or patient quotes.\n"
    "   - listing_management: the exact Google Business Profile fields to standardize/claim "
    "(name, address, phone, categories, hours) using FACTS, with [VERIFY: ...] for anything "
    "unknown, and the steps to consolidate duplicate/unclaimed listings.\n"
    "Keep each draft self-contained and immediately usable. Return one entry per finding via "
    "submit_drafts."
)


def draft_findings(entity_name: str, location: str, entity_kind: str,
                   facts: dict, findings: list) -> dict:
    """Return {finding_id: draft_content} for the given findings. Never raises —
    returns {} on failure so the caller can proceed without drafts."""
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

    find_lines = []
    for f in draftable:
        find_lines.append(
            f"[{f.get('finding_id')}] platform={f.get('platform')} "
            f"remediation_type={f.get('remediation_type')}\n"
            f"  issue: {f.get('teaser_summary')}\n"
            f"  current: {f.get('current_state')}\n  expected: {f.get('expected_state')}"
        )

    prompt = ("FACTS (the ONLY facts you may treat as true):\n" + "\n".join(facts_lines)
              + "\n\nFINDINGS TO DRAFT:\n" + "\n\n".join(find_lines)
              + "\n\nDraft publication-ready content for each finding. Remember: facts only, "
                "[VERIFY: ...] for anything unknown, no invented citations.")

    try:
        resp = client.messages.create(
            model=_MODEL, max_tokens=8192, tools=[_DRAFT_TOOL],
            tool_choice={"type": "tool", "name": "submit_drafts"},
            system=_SYSTEM, messages=[{"role": "user", "content": prompt}],
        )
    except Exception:
        return {}
    out = {}
    for block in resp.content:
        if block.type == "tool_use" and block.name == "submit_drafts":
            d = block.input if isinstance(block.input, dict) else json.loads(block.input)
            for item in d.get("drafts", []):
                fid = item.get("finding_id")
                dc = (item.get("draft_content") or "").strip()
                if fid and dc:
                    out[fid] = dc
    return out
