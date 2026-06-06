from __future__ import annotations

import httpx

from .config import settings


class DashboardAPIClient:
    """Thin wrapper around the product's backend API for cross-checking dashboard data."""

    def __init__(self) -> None:
        self._client = httpx.Client(
            base_url=settings.dashboard_api_base_url,
            headers={"Authorization": f"Bearer {settings.dashboard_api_key}"},
            timeout=30,
        )

    def get(self, path: str, **params) -> dict:
        r = self._client.get(path, params=params)
        r.raise_for_status()
        return r.json()

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
