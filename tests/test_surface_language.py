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
    "Head B": (
        "the classifier's internal name — the page says 'usual level', not the head"
    ),
    "EWMA": "an estimator noun on a page about what just arrived",
    "120 years": (
        "the archive floor is 1972; the slider and the lede have to say so"
    ),
    "hundred and twenty years": (
        "the same 1905 claim in longhand — Landing used it to dodge '120 years'"
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
    initiator_name / target_name instead; Intel's folded feed caught up."""
    wire = _strip_comments(
        (WEB / "components" / "WireList.tsx").read_text(encoding="utf-8")
    )
    assert "item.name" not in wire, (
        "the feed is rendering the coded event name; compose a headline from "
        "named fields in lib/story.ts (wireHeadline) instead"
    )
    assert "wireHeadline" in wire
    assert not (WEB / "components" / "WirePage.tsx").exists()


def test_the_relationship_page_leads_with_three_reads_not_a_hostility_ladder() -> None:
    """Standing, posture, intensity — not tensionSentence over the bands."""
    rel = _strip_comments(
        (WEB / "components" / "RelationshipPage.tsx").read_text(encoding="utf-8")
    )
    assert "tensionSentence" not in rel
    assert "relationshipStandfirst" in rel


def test_the_slider_opens_at_the_archive_floor() -> None:
    slider = (WEB / "components" / "TimeSlider.tsx").read_text(encoding="utf-8")
    assert "YEAR_MIN = 1972" in slider
    assert "YEAR_MIN = 1905" not in slider


def test_the_landing_lede_opens_at_the_archive_floor() -> None:
    """The door used to claim a hundred and twenty years. The floor is 1972."""
    landing = _strip_comments(
        (WEB / "components" / "Landing.tsx").read_text(encoding="utf-8")
    )
    assert "1972" in landing
    assert "priced." not in landing
    assert "/intel" in landing


def test_intel_and_the_wire_are_one_package() -> None:
    """Intel is the feed. The Wire page is gone; old hashes still land here."""
    sidebar = _strip_comments(
        (WEB / "components" / "Sidebar.tsx").read_text(encoding="utf-8")
    )
    assert "'/intel', 'Intel'" in sidebar
    assert "'/wire', 'Wire'" not in sidebar
    assert "'/situation', 'Situation'" not in sidebar
    intel = _strip_comments(
        (WEB / "components" / "IntelPage.tsx").read_text(encoding="utf-8")
    )
    assert "Ask the desk" not in intel
    assert "Read this" in intel
    assert "summonDesk" in intel
    assert "situationLede" in intel
    assert "AgentDesk" not in intel
    assert "A follow-up" not in intel
    assert "desk-ask" not in intel
    assert "new reading" not in intel
    assert "brief()" not in intel
    assert "WireFeedBeats" in intel
    assert "getWireLive" in intel
    assert "intelTrafficFigure" in intel
    assert "explanation[0]" not in intel
    app = _strip_comments((WEB / "App.tsx").read_text(encoding="utf-8"))
    assert "AgentModal" in app
    assert "WirePage" not in app
    assert "route.startsWith('/wire')" in app
    # Landing returns before the working frame, so the FAB is not on `/`.
    landing_return = app.find("return <Landing")
    modal_jsx = app.find("<AgentModal")
    assert landing_return != -1 and modal_jsx != -1 and landing_return < modal_jsx
    modal_src = _strip_comments(
        (WEB / "components" / "AgentModal.tsx").read_text(encoding="utf-8")
    )
    assert "onIntel" not in modal_src
    assert "onLanding" in modal_src
    assert "AgentDesk" in modal_src
    assert "deskOpen" in modal_src
    session = _strip_comments(
        (WEB / "components" / "AgentSession.tsx").read_text(encoding="utf-8")
    )
    assert "summonDesk" in session
    assert "deskOpen" in session
    desk = _strip_comments(
        (WEB / "components" / "AgentDesk.tsx").read_text(encoding="utf-8")
    )
    assert "A follow-up" not in desk
    assert "A question for the desk" in desk


def test_wire_headlines_use_cameo_roots_not_only_quad_four() -> None:
    """Quad 4 is not always 'used force toward'. The composer reads CAMEO."""
    story = _strip_comments((WEB / "lib" / "story.ts").read_text(encoding="utf-8"))
    assert "CAMEO_ACT" in story
    assert "exhibited force toward" in story
    assert "thirdCountryForce" in story
    assert "used force in" in story
    # The quad fallback may still exist; it must not be the only verb.
    assert story.count("used force toward") == 1


def test_the_desk_renders_an_article_not_a_preformatted_blob() -> None:
    """The opening reading is grafs. Method is a disclosure, not a standfirst."""
    desk = _strip_comments(
        (WEB / "components" / "AgentDesk.tsx").read_text(encoding="utf-8")
    )
    assert "parseDeskProse" in desk
    assert "desk-lede" in desk
    assert "Disclosure" in desk
    assert "white-space: pre-wrap" not in desk
    assert "standfirst={method}" not in desk
    css = (WEB / "styles.css").read_text(encoding="utf-8")
    assert "white-space: pre-wrap" not in css
    assert "--type-display" in css
    assert css.count(".story-head h1") == 1
    assert css.count(".beat-head h2") == 1


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


def test_working_pages_use_the_desk_layout_not_a_reading_column() -> None:
    """Markets, games, relationships, network and cases share Intel's desk
    measure — not one narrow `.reading-column`. Explorer and the long-form
    case-study article are deliberately left alone."""
    for name in (
        "MarketsPage.tsx",
        "GamesPage.tsx",
        "RelationshipPage.tsx",
        "NetworkPage.tsx",
        "CasesPage.tsx",
    ):
        text = _strip_comments((WEB / "components" / name).read_text(encoding="utf-8"))
        assert "desk-page" in text, f"{name} never took the desk-page shell"
        assert "reading-column" not in text, f"{name} still wraps in reading-column"
    css = (WEB / "styles.css").read_text(encoding="utf-8")
    assert ".desk-page" in css
    assert ".desk-grid" in css
    assert ".intel-page" in css
    assert ".intel-grid" in css


def test_the_case_desk_builds_from_the_wire() -> None:
    """`/cases` is a builder for every region, not only pack-declared studies."""
    cases = _strip_comments((WEB / "components" / "CasesPage.tsx").read_text(encoding="utf-8"))
    assert "Build a study" in cases
    assert "getWire" in cases
    assert "wireHeadline" in cases
    assert "/case/dynamic?event=" in cases
    assert "AgentDesk" not in cases
    # An empty pack must still offer the picker — not declare the page empty.
    assert "the builder" in cases.lower() or "Build a study" in cases


def test_case_study_narrates_on_click_not_on_load() -> None:
    """The desk reads a study when asked. GET stays the measured record."""
    view = _strip_comments(
        (WEB / "components" / "CaseStudyView.tsx").read_text(encoding="utf-8")
    )
    assert "postCaseNarrate" in view
    assert "Read this" in view
    load = re.search(r"useEffect\((.*?)\[\s*slug\s*\]\s*\)", view, re.S)
    assert load, "the study fetch on slug is missing"
    assert "postCaseNarrate" not in load.group(1), (
        "narrate is firing in the load effect; it must sit in a click handler"
    )
    click = re.search(r"const onRead\s*=\s*\(\)\s*=>\s*\{(.*?)\n  \}", view, re.S)
    assert click and "postCaseNarrate" in click.group(1)


def test_ordinary_events_are_not_sold_as_case_studies() -> None:
    """A GDELT wire event is not a narrated pack study. 'the study →' sent
    readers to `/case/dynamic?event=` which 404s for live rows and misnames
    the measured-impact view for the rest."""
    wire = _strip_comments(
        (WEB / "components" / "WireList.tsx").read_text(encoding="utf-8")
    )
    assert "the study →" not in wire
    assert "onStudy" not in wire
    assert "/case/dynamic?event=" not in wire
    desk = _strip_comments((WEB / "lib" / "desk.ts").read_text(encoding="utf-8"))
    assert "/case/dynamic?event=" not in desk


def test_source_hrefs_are_gated_not_raw_mention_urls() -> None:
    """item.source_url as href was the baseball article: GDELT SOURCEURL."""
    offenders = []
    for path, text in _sources():
        if "href={item.source_url}" in text or "href={s.url}" in text:
            offenders.append(path.name)
    assert not offenders, (
        "a component is href-ing a raw source field; gate through citableUrl:\n  "
        + "\n  ".join(offenders)
    )
    wire = _strip_comments(
        (WEB / "components" / "WireList.tsx").read_text(encoding="utf-8")
    )
    assert "citableUrl" in wire
    explorer = _strip_comments(
        (WEB / "components" / "Explorer.tsx").read_text(encoding="utf-8")
    )
    assert "citableUrl" in explorer


def test_priced_courses_can_be_marked_untrusted() -> None:
    story = _strip_comments((WEB / "lib" / "story.ts").read_text(encoding="utf-8"))
    assert "pricingTrustSentence" in story
    assert "marked untrusted" in story
    games = _strip_comments(
        (WEB / "components" / "GamesPage.tsx").read_text(encoding="utf-8")
    )
    assert "pricingTrustSentence" in games


def test_the_case_desk_opens_a_composed_reading() -> None:
    cases = _strip_comments(
        (WEB / "components" / "CasesPage.tsx").read_text(encoding="utf-8")
    )
    assert "composed reading" in cases.lower() or "composed" in cases.lower()
