"""What may be presented as a source href.

Every sourced fact cites a Source node. That node's `url` is sometimes a real
document (COW, a CRS report, the GDELT export index) and sometimes a local
pack path (`packs/mena/marquee_events.yaml`). Only an http(s) URL with a host
is a citation the surface may click.

GDELT's per-row SOURCEURL is a different field: a mention string, not a
verified article about the coded event. Sports wire wearing a roster actor
name is a known class of defect. Do not run this helper over SOURCEURL and
treat a well-formed result as a citation — well-formed is not verified.
"""

from __future__ import annotations

from urllib.parse import urlparse

_SCHEMES = frozenset({"http", "https"})


def citable_url(url: object) -> str | None:
    """Return `url` only when it is an http(s) document address.

    Relative paths, blank strings, and non-web schemes are names, not links.
    """
    if not isinstance(url, str):
        return None
    text = url.strip()
    if not text or len(text) > 2048:
        return None
    parsed = urlparse(text)
    if parsed.scheme not in _SCHEMES:
        return None
    host = (parsed.hostname or "").strip(".")
    if not host or "." not in host:
        return None
    return text
