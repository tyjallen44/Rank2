"""Assemble verified evidence (census + real Google reads) for the analysis.

This is the bridge between the raw data clients and the prompt. It builds the
denominator (CMS for hospitals, NPPES sizing for specialties), attaches a real
Google read to each candidate, and renders a compact evidence block that the
analysis prompt is anchored to — so the AI Visibility Score is scored against
fetched numbers rather than model recall.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from . import cms, nppes, places
from .places import Footprint, GoogleRead


def normalize_name(name: str) -> str:
    """Loose key for matching a ranked-provider name back to its evidence read."""
    return re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()


@dataclass
class ProviderEvidence:
    name: str
    google: GoogleRead
    footprint: Optional[Footprint] = None
    cms_rating: Optional[int] = None
    hospital_type: Optional[str] = None
    emergency_services: Optional[bool] = None

    def as_line(self) -> str:
        bits = [f"• {self.name}"]
        if self.cms_rating is not None:
            bits.append(f"CMS overall {self.cms_rating}★")
        if self.hospital_type:
            ed = "ED" if self.emergency_services else "no ED"
            bits.append(f"{self.hospital_type}, {ed}")
        bits.append(f"Google: {self.google.as_line()}")
        if self.footprint and self.footprint.listings_sampled > 1:
            bits.append(f"Footprint: {self.footprint.as_line()}")
        return " | ".join(bits)


@dataclass
class MarketEvidence:
    location: str
    mode: str                         # "hospital" | "specialty"
    specialty: Optional[str] = None
    providers: list[ProviderEvidence] = field(default_factory=list)
    registry_total: int = 0           # M in "covered N of M"
    coverage_note: str = ""
    extra_context: str = ""           # NPPES sizing for specialty mode

    def google_index(self) -> dict[str, GoogleRead]:
        return {normalize_name(p.name): p.google for p in self.providers}

    def footprint_index(self) -> dict[str, Footprint]:
        return {normalize_name(p.name): p.footprint for p in self.providers if p.footprint}

    def to_prompt_block(self) -> str:
        lines = [
            "=== VERIFIED MARKET EVIDENCE (anchor your scores to this) ===",
            f"Location: {self.location}",
            f"Mode: {self.mode}" + (f" — specialty: {self.specialty}" if self.specialty else ""),
            self.coverage_note,
        ]
        if self.extra_context:
            lines.append(self.extra_context)
        if self.providers:
            lines.append("")
            lines.append("Census with fetched Google reads:")
            lines.extend(p.as_line() for p in self.providers)
        lines.append("=== END EVIDENCE ===")
        return "\n".join(l for l in lines if l)


def gather_hospital_evidence(
    city: str,
    state: str,
    counties: list[str] | None = None,
    *,
    api_key: str | None = None,
    max_google: int = 30,
) -> MarketEvidence:
    """CMS hospital census for the metro + a real Google read per material hospital."""
    census = cms.list_hospitals(state, cities=None if counties else [city], counties=counties)
    material = [h for h in census if h.is_material]
    others = [h for h in census if not h.is_material]

    providers: list[ProviderEvidence] = []
    for h in material[:max_google]:
        read, footprint = places.fetch_provider(h.name, h.city, state, api_key=api_key)
        providers.append(
            ProviderEvidence(
                name=h.name,
                google=read,
                footprint=footprint,
                cms_rating=h.overall_rating,
                hospital_type=h.hospital_type,
                emergency_services=h.emergency_services,
            )
        )

    excluded = len(others) + max(0, len(material) - max_google)
    coverage = (
        f"Covered {len(providers)} of {len(census)} CMS-listed hospitals in the metro "
        f"(material acute-care + ED ranked first; {len(others)} non-material "
        f"[critical-access / specialty / no-ED] noted below the fold"
        + (f"; {len(material) - max_google} material beyond the Google-fetch cap" if len(material) > max_google else "")
        + ")."
        if census else
        "CMS returned no hospitals for the requested metro — widen the county set or verify the location."
    )
    return MarketEvidence(
        location=f"{city}, {state}",
        mode="hospital",
        providers=providers,
        registry_total=len(census),
        coverage_note=coverage,
    )


def gather_specialty_context(
    city: str,
    state: str,
    specialty: str,
    *,
    taxonomy: str | None = None,
    api_key: str | None = None,
    rebrand_check_limit: int = 15,
) -> MarketEvidence:
    """NPPES market sizing for a specialty (no per-practice census up front).

    Practice-level Google reads are attached *after* the model names the
    practices (see analyzer), since NPPES enumerates clinicians, not practices.

    As a pre-pass, the top NPPES candidate names are queried against Google
    Places. When Google returns a business with a completely different name at
    the same location, that is flagged in the evidence block as a likely rebrand
    so Claude uses the current public name rather than the stale NPPES record.
    """
    market = nppes.search_specialty_market(city, state, taxonomy or specialty)

    # Rebrand detection: NPPES records rarely update when a group renames.
    # Query Google Places for each top candidate; a "none" name-match where
    # Google returns a different real business is a strong rebrand signal.
    rebrand_notes: list[str] = []
    for name in market.org_names[:rebrand_check_limit]:
        read = places.fetch_google_rating(name, city, state, api_key=api_key)
        if (
            read.matched_name
            and read.name_match == "none"
            and read.matched_name.strip().lower() != name.strip().lower()
        ):
            rebrand_notes.append(
                f"'{name}' → Google Places returned '{read.matched_name}' "
                f"(name mismatch — likely rebrand or name change; use current name)"
            )

    extra_context = market.as_context()
    if rebrand_notes:
        extra_context += (
            "\n\nPossible rebrands detected (NPPES name → current Google Business name):\n"
            + "\n".join(f"• {note}" for note in rebrand_notes)
        )

    coverage = (
        f"NPPES enumerated ~{market.provider_count}{'+' if market.capped else ''} "
        f"individual '{specialty}' clinicians in {city}, {state}; practice grouping "
        f"is resolved in analysis and every ranked practice is Google-verified."
    )
    return MarketEvidence(
        location=f"{city}, {state}",
        mode="specialty",
        specialty=specialty,
        registry_total=market.provider_count,
        coverage_note=coverage,
        extra_context=extra_context,
    )
