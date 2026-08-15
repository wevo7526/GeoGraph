"""SEC EDGAR 13F → FLOW edges: the SWF money layer (build-spec section 5.2).

A 13F-HR is a quarterly information table of US-listed long equity. The FLOW
edge aggregates one filing to ONE number — total reported value, filer →
US equity market — because that is what the filing can honestly support: a
COARSE, LAGGED (45-day), US-LONG-ONLY view, and every surface serving it
says so (the Flow class description carries the same caveat).

Deterministic quirks handled by date, not guessed:
- REPORTING UNITS CHANGED: information-table values are THOUSANDS of dollars
  for periods before 2023-01-01 and whole dollars from then on (the SEC's
  own transition). The parser normalizes to dollars by report period.
- Filer CIKs are CURATED IN THE PACK (assets.yaml `swf_filers`), resolved
  once against EDGAR's company search and cited — never guessed at runtime.
  ADIA has no locatable 13F filer as of 2026-08; its absence from the pack
  is that finding, recorded.

SEC asks for a descriptive User-Agent and ~10 req/s max; the fetcher
identifies itself and sleeps between requests.
"""

from __future__ import annotations

import json
import time
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any

from core.graph import kuzu_store

SOURCE_EDGAR = "source:edgar-13f"

_USER_AGENT = "GeoGraph research (wfe818@gmail.com)"
_PAUSE_SECONDS = 0.15

#: Report periods from this date onward file values in DOLLARS; earlier
#: filings are in THOUSANDS.
_DOLLARS_FROM = "2023-01-01"


def _get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
        payload = bytes(response.read())
    time.sleep(_PAUSE_SECONDS)
    return payload


def parse_information_table(xml_bytes: bytes, *, report_date: str) -> int:
    """Total reported value of one 13F information table, in DOLLARS.

    Pure — testable on fixture XML. Namespace-agnostic on purpose: filers
    emit the table under several namespace spellings, and the tag LOCAL
    names are the stable part of the format.
    """
    root = ET.fromstring(xml_bytes)
    total = 0
    seen = 0
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] == "value" and element.text:
            total += int(float(element.text.strip().replace(",", "")))
            seen += 1
    if seen == 0:
        raise ValueError("no <value> entries — this is not a 13F information table")
    if report_date < _DOLLARS_FROM:
        total *= 1000
    return total


def fetch_filings(cik: int, *, limit: int = 8) -> list[dict[str, Any]]:
    """The filer's recent 13F-HR filings: report date, accession, total USD.

    Reads EDGAR's submissions JSON, then each filing's index to locate the
    information-table XML (its filename varies; the primary document is the
    cover page, not the table).
    """
    padded = f"{cik:010d}"
    submissions = json.loads(_get(f"https://data.sec.gov/submissions/CIK{padded}.json"))
    recent = submissions.get("filings", {}).get("recent", {})
    out: list[dict[str, Any]] = []
    for form, accession, report_date in zip(
        recent.get("form", []),
        recent.get("accessionNumber", []),
        recent.get("reportDate", []),
        strict=False,
    ):
        if form != "13F-HR" or not report_date:
            continue
        plain = accession.replace("-", "")
        base = f"https://www.sec.gov/Archives/edgar/data/{cik}/{plain}"
        index = json.loads(_get(f"{base}/index.json"))
        table_names = [
            item["name"]
            for item in index.get("directory", {}).get("item", [])
            if item["name"].lower().endswith(".xml")
            and "primary_doc" not in item["name"].lower()
        ]
        if not table_names:
            continue  # a cover-only index is a filing the loader cannot read
        total = parse_information_table(
            _get(f"{base}/{sorted(table_names)[0]}"), report_date=report_date
        )
        out.append({"report_date": report_date, "accession": accession,
                    "value_usd": total})
        if len(out) >= limit:
            break
    return out


def load_flows(
    conn: Any,
    filers: list[dict[str, Any]],
    *,
    market_node_id: str,
    limit_per_filer: int = 8,
) -> int:
    """FLOW edges for each pack-declared filer: actor → the US equity market,
    one edge per quarter (`as_of` is identity). Returns edges written."""
    # Source BEFORE the edges that cite it — self-sufficient, so a graph
    # seeded without the one pack that happens to declare this source still
    # satisfies the provenance invariant. Field-identical to the mena pack's
    # declaration: whichever writes second must not change the description.
    kuzu_store.merge_nodes(conn, "Source", [{
        "node_id": SOURCE_EDGAR,
        "name": "SEC EDGAR 13F filings",
        "kind": "feed",
        "url": "https://www.sec.gov/cgi-bin/browse-edgar",
        "citation": "Quarterly 13F-HR information tables.",
    }])
    edges: list[dict[str, Any]] = []
    for filer in filers:
        for filing in fetch_filings(int(filer["cik"]), limit=limit_per_filer):
            edges.append({
                "src": filer["actor"],
                "dst": market_node_id,
                "as_of": filing["report_date"],
                "value_usd": filing["value_usd"],
                "source_id": SOURCE_EDGAR,
            })
    return kuzu_store.merge_edges(conn, "FLOW", edges)
