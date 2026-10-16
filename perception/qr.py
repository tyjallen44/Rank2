"""QR codes for the association attribution block (4.6). Fail-soft: '' when segno is missing."""
from __future__ import annotations

import base64


def qr_svg_data_uri(url: str, scale: int = 4) -> str:
    """An SVG data URI for `url`, or '' when the library is unavailable or the URL is empty."""
    if not (url or "").strip():
        return ""
    try:
        import segno
        svg = segno.make(url.strip(), error="m").svg_inline(scale=scale, border=1, dark="#0F4146", light=None)
    except Exception:
        return ""
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode("ascii")
