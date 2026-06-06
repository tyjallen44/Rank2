from __future__ import annotations

import nltk
from nltk.sentiment.vader import SentimentIntensityAnalyzer

from .models import SentimentLabel

nltk.download("vader_lexicon", quiet=True)

_analyzer: SentimentIntensityAnalyzer | None = None


def _get_analyzer() -> SentimentIntensityAnalyzer:
    global _analyzer
    if _analyzer is None:
        _analyzer = SentimentIntensityAnalyzer()
    return _analyzer


def analyze(text: str) -> tuple[SentimentLabel, float]:
    """Return (label, score) where score is -1.0 (negative) to 1.0 (positive)."""
    scores = _get_analyzer().polarity_scores(text)
    compound = scores["compound"]
    if compound >= 0.05:
        label = SentimentLabel.positive
    elif compound <= -0.05:
        label = SentimentLabel.negative
    else:
        label = SentimentLabel.neutral
    return label, compound
