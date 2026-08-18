"""THE SURFACE'S VOCABULARY, held by a test.

The 2026-08-17 rewrite moved every page's first sentence out of the payload's
`explanation` and into a composer (`web/src/lib/story.ts`), and swept the
estimator's nouns off the reader's half of the product. Both are the kind of
change that decays: the next person to add a beat reaches for the paragraph the
backend already wrote, and the next payload field arrives with a name like
`car_0_3` and gets rendered because it was there.

These are source checks, not render tests — the web app has no test runner, and
these two rules are decidable from the source. They are deliberately narrow:
each banned token is distinctive enough that any occurrence in a component is a
reader-facing occurrence.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1] / "web" / "src"
COMPONENTS = sorted((WEB / "components").rglob("*.tsx"))


#: Comments are where the REASONS live, and the reasons name the things that
#: were removed — this file's own bans are quoted in the components that used to
#: violate them. Strip them before scanning, or the record of a fix reads as the
#: defect.
_BLOCK = re.compile(r"/\*.*?\*/", re.S)
_LINE = re.compile(r"^\s*//.*$", re.M)


def _strip_comments(text: str) -> str:
    return _LINE.sub("", _BLOCK.sub("", text))


def _sources() -> list[tuple[Path, str]]:
    return [(p, _strip_comments(p.read_text(encoding="utf-8"))) for p in COMPONENTS]


def test_there_are_components_to_check() -> None:
    # A rename that empties the glob would make every test below vacuous.
    assert len(COMPONENTS) >= 8, "the component glob found nothing to check"


def test_no_page_renders_the_audit_paragraph_as_its_lede() -> None:
    """`explanation` is the AUDIT, and it belongs under a disclosure.

    It exists to satisfy build-spec §17 — every number in the prose is a field —
    and it does that well. It is not a lede: the region map's first sentence ran
    to a hundred words with nested parentheses and named the solver twice, and
    the pair page's opened on a CINC ratio and a Bayes filter. A page's first
    sentence is composed in TSX from named fields (`lib/story.ts`), which is
    also what stops a prose defect shipping without a frontend change.
    """
    offenders = []
    for path, text in _sources():
        # `standfirst={...}` / `title={...}` whose expression reaches for the
        # backend's paragraph. Matches across the attribute's own line only,
        # which is how these are written.
        for attr in ("standfirst", "title"):
            for match in re.finditer(rf"{attr}=\{{([^}}\n]*)", text):
                expression = match.group(1)
                if re.search(r"explanation\s*\[\s*0\s*\]", expression):
                    offenders.append(f"{path.name}: {attr}={{{expression.strip()}")
    assert not offenders, (
        "a page is using the backend's audit paragraph as its first sentence; "
        "compose it from fields in lib/story.ts instead:\n  " + "\n  ".join(offenders)
    )


#: Tokens that are the machine's names for things, not a reader's. Each one was
#: on the product surface on 2026-08-16 and is a defect wherever it returns.
MACHINE_TOKENS = {
    "·⌁": "a private glyph for 'this pair's kernel was tilted by the model'",
    "measuring boot": "an infrastructure noun on a page about a relationship",
    "gated within-dyad ridge": "the ml-spec's name for the model, on the hero page",
    "models/intensity.json": "an artifact path, rendered",
    "CAR–0": "the event study's window name, rendered",
    "tone_label": (
        "the mean-Goldstein label, which scored the US and China 'friendly' — "
        "core/games/scenarios.py forbids presenting it as a characterisation"
    ),
    "exact benchmark": (
        "the LP correlated equilibrium is an audit of distance from Nash, not Nash itself"
    ),
    "What is actionable now": (
        "the live wire is intel scored against a snapshot baseline, not a blotter"
    ),
}


@pytest.mark.parametrize("token", sorted(MACHINE_TOKENS))
def test_the_machines_vocabulary_stays_off_the_page(token: str) -> None:
    hits = [path.name for path, text in _sources() if token in text]
    assert not hits, (
        f"{token!r} is back in {', '.join(hits)} — {MACHINE_TOKENS[token]}"
    )


def test_the_wire_does_not_render_coded_event_names() -> None:
    """The event's own `name` is CAMEO vocabulary. The globe already ships
    initiator_name / target_name instead; the wire page caught up."""
    wire = _strip_comments(
        (WEB / "components" / "WirePage.tsx").read_text(encoding="utf-8")
    )
    assert "item.name" not in wire, (
        "WirePage is rendering the coded event name; compose a headline from "
        "named fields in lib/story.ts (wireHeadline) instead"
    )
    assert "wireHeadline" in wire


def test_the_relationship_page_leads_with_three_reads_not_a_hostility_ladder() -> None:
    """Standing, posture, intensity — not tensionSentence over the bands."""
    rel = _strip_comments(
        (WEB / "components" / "RelationshipPage.tsx").read_text(encoding="utf-8")
    )
    assert "tensionSentence" not in rel
    assert "relationshipStandfirst" in rel


def test_a_dyad_id_is_never_built_into_a_sentence() -> None:
    """Ids are for links and keys. A reader who sees `dyad:cow-365--cow-372`
    is reading a database row — which the markets page's duration bars did,
    wherever the payload's `dyad_name` was missing."""
    pattern = re.compile(r"['\"`]dyad:")
    offenders = []
    for path, text in _sources():
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith(("//", "*", "/*")):
                continue
            # A ROUTE or a React KEY may carry an id; a sentence may not.
            if any(ok in line for ok in ("encodeURIComponent", "key:", "key=")):
                continue
            if pattern.search(line):
                offenders.append(f"{path.name}: {stripped[:80]}")
    assert not offenders, (
        "a dyad id is being written into user-facing text:\n  " + "\n  ".join(offenders)
    )
