"""Live data sources that ground the AI Visibility Score in verifiable evidence.

Unlike the legacy (unused) ``perception.sources`` Playwright scrapers, these
clients hit stable public APIs:

- ``places``  — Google Places API v1 (real Google rating + review count + footprint)
- ``cms``     — CMS Care Compare (authoritative hospital census + overall star rating)
- ``nppes``   — NPPES NPI registry (specialty-provider discovery / market sizing)

``evidence.gather_evidence`` assembles a census + verified Google reads into the
``MarketEvidence`` block that the analysis prompt is anchored to.
"""
