"""NPPES NPI registry — specialty-provider discovery and market sizing.

For a specialty run, NPPES (the free national NPI registry) grounds *how big*
the market actually is — the provider count and the distinct practice/org names
operating in a city for a given taxonomy. NPPES enumerates individual
clinicians, so it is not a clean practice-level census; we use it to (a) size
the denominator and (b) surface candidate organization names, which the analysis
then resolves and Google-verifies. Labeled as a sample, never as a census.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

import httpx

_BASE = "https://npiregistry.cms.hhs.gov/api/"
_PAGE = 200  # NPPES hard max per request


@dataclass
class SpecialtyMarket:
    city: str
    state: str
    taxonomy: str
    provider_count: int = 0          # individual NPIs found (sampled if capped)
    capped: bool = False             # True if we hit the result cap
    org_names: list[str] = field(default_factory=list)  # distinct candidate practice/org names

    def as_context(self) -> str:
        count = f"{self.provider_count}{'+' if self.capped else ''}"
        orgs = "; ".join(self.org_names[:25]) if self.org_names else "no organization names enumerated"
        return (
            f"NPPES registry shows ~{count} '{self.taxonomy}' providers enumerated in "
            f"{self.city}, {self.state}. Candidate practice/organization names: {orgs}."
        )


def search_specialty_market(
    city: str,
    state: str,
    taxonomy: str,
    *,
    max_records: int = 600,
    timeout: float = 30.0,
) -> SpecialtyMarket:
    """Enumerate providers for a taxonomy in a city and roll up org names."""
    city = city.strip()
    state = state.strip().upper()
    market = SpecialtyMarket(city=city, state=state, taxonomy=taxonomy)
    org_counter: Counter[str] = Counter()
    skip = 0

    while skip < max_records:
        params = {
            "version": "2.1",
            "city": city,
            "state": state,
            "taxonomy_description": taxonomy,
            "limit": _PAGE,
            "skip": skip,
        }
        try:
            resp = httpx.get(_BASE, params=params, timeout=timeout)
            resp.raise_for_status()
            payload = resp.json()
        except (httpx.HTTPError, ValueError):
            break

        results = payload.get("results", []) or []
        if not results:
            break
        market.provider_count += len(results)

        for rec in results:
            basic = rec.get("basic", {}) or {}
            org = basic.get("organization_name") or basic.get("authorized_official_organization_name")
            if org:
                org_counter[org.strip().title()] += 1
            for other in rec.get("other_names", []) or []:
                name = other.get("organization_name")
                if name:
                    org_counter[name.strip().title()] += 1

        if len(results) < _PAGE:
            break
        skip += _PAGE

    market.capped = skip >= max_records
    market.org_names = [name for name, _ in org_counter.most_common(40)]
    return market
