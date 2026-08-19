"""A source href is a document, not a GDELT mention string.

GDELT SOURCEURL is often a sports wire that happened to share an actor name.
A well-formed URL is not a verified citation of the coded event.
"""

from __future__ import annotations

import inspect

from core.api.routers import events as events_router
from core.cite import citable_url
from core.ingestion import gdelt


def test_citable_url_accepts_http_documents_only() -> None:
    assert citable_url(gdelt.SOURCE_GDELT_URL) == gdelt.SOURCE_GDELT_URL
    assert citable_url("https://correlatesofwar.org/data-sets/mids/")
    assert citable_url("packs/mena/marquee_events.yaml") is None
    assert citable_url("javascript:alert(1)") is None
    assert citable_url("file:///etc/passwd") is None
    assert citable_url("") is None
    assert citable_url(None) is None
    assert citable_url("https://") is None
    # Well-formed is not verified. This helper must not be the only gate on
    # GDELT SOURCEURL — a baseball story would pass.
    mlb = "https://www.mlb.com/news/unrelated-baseball-story"
    assert citable_url(mlb) == mlb


def test_the_live_wire_does_not_cite_gdelt_sourceurl() -> None:
    source = inspect.getsource(events_router.wire_live)
    assert 'row.get("source_url")' not in source
    assert "SOURCE_GDELT_URL" in source
    assert "source_name" in source


def test_gdelt_as_a_source_cites_the_dataset_not_a_mention() -> None:
    payload = events_router._cited_source(
        gdelt.SOURCE_GDELT,
        {"url": "https://www.mlb.com/news/unrelated-baseball-story"},
    )
    assert payload["url"] == gdelt.SOURCE_GDELT_URL
    assert payload["name"] == "GDELT"
    pack = events_router._cited_source(
        "source:mena-marquee",
        {"name": "MENA marquee list", "url": "packs/mena/marquee_events.yaml"},
    )
    assert pack["url"] is None
    assert pack["name"] == "MENA marquee list"
