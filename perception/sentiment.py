from __future__ import annotations

from openai import OpenAI

from .config import settings
from .models import SentimentLabel

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=settings.openai_api_key)
    return _client


def analyze(text: str) -> tuple[SentimentLabel, float]:
    """Return (label, score) where score is -1.0 (negative) to 1.0 (positive)."""
    response = _get_client().chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a sentiment classifier for healthcare reviews. "
                    "Respond with JSON only: {\"label\": \"positive\"|\"neutral\"|\"negative\", \"score\": <float -1.0 to 1.0>}"
                ),
            },
            {"role": "user", "content": text},
        ],
        response_format={"type": "json_object"},
        temperature=0,
    )
    import json
    data = json.loads(response.choices[0].message.content)
    return SentimentLabel(data["label"]), float(data["score"])
