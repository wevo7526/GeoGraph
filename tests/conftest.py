"""Test isolation for the wire corpus.

THE CORPUS IS OPT-IN UNDER TEST. Every bulk consumer reads corpus-first, and
the real artifacts ship in the repository — so without this fixture a test
that builds a three-event graph gets 1.33M real events unioned into it and
asserts against the archive instead of its fixture. Four tests failed exactly
that way the night the corpus landed.

The default here is the safe direction: tests see NO corpus and every
corpus-first path falls back to the graph, which is precisely the pre-corpus
behavior the fixtures were written against. A test that wants the real thing
(tests/test_wire.py) opts back in with the `real_corpus` fixture.
"""

from __future__ import annotations

import pytest

from core.wire import corpus, serving


@pytest.fixture(autouse=True)
def _isolated_corpus(tmp_path, monkeypatch):
    """Point the corpus at an empty directory and clear the warmed caches."""
    monkeypatch.setenv("GEOGRAPH_DERIVED_DIR", str(tmp_path / "no-corpus"))
    serving.reset()
    yield
    # The next test's monkeypatch context restores the env; the serving cache
    # must be dropped again so tables warmed from THIS test's view of the
    # corpus cannot leak into the next.
    serving.reset()


@pytest.fixture
def real_corpus(monkeypatch):
    """Opt back in to the repository's shipped artifacts."""
    monkeypatch.delenv("GEOGRAPH_DERIVED_DIR", raising=False)
    serving.reset()
    yield corpus
    serving.reset()
