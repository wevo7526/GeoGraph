"""Classifier Head A — the one place a model touches the archive.

These tests never call the API. What matters is the BOUNDARY: the model
returns a code, the deterministic layer derives everything else from it, and
a code the archive cannot score is refused rather than coerced.
"""

from __future__ import annotations

import json
import sys
import types
from typing import Any

import pytest

from core.classifier import typing as event_typing


class _Response:
    def __init__(
        self,
        payload: dict[str, Any] | None,
        *,
        finish_reason: str = "stop",
        refusal: str | None = None,
    ):
        content = json.dumps(payload) if payload is not None else ""
        message = types.SimpleNamespace(content=content, refusal=refusal)
        self.choices = [
            types.SimpleNamespace(finish_reason=finish_reason, message=message)
        ]


class _FakeOpenAI:
    """Records the request so the call's shape can be asserted."""

    last_kwargs: dict[str, Any] = {}
    response: _Response | None = None

    def __init__(self, **_: Any) -> None:
        self.chat = types.SimpleNamespace(
            completions=types.SimpleNamespace(create=self._create)
        )

    def _create(self, **kwargs: Any) -> _Response:
        type(self).last_kwargs = kwargs
        response = type(self).response
        assert response is not None, "set _FakeOpenAI.response before calling"
        return response


@pytest.fixture()
def fake_sdk(monkeypatch):
    module = types.ModuleType("openai")
    module.OpenAI = _FakeOpenAI  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "openai", module)
    _FakeOpenAI.last_kwargs = {}
    return _FakeOpenAI


def test_the_model_types_the_event_and_the_crosswalk_prices_it(fake_sdk):
    # The model returns 195 (aerial weapons) and NOTHING else that matters:
    # quad_class and goldstein come from the codebook, not the model.
    fake_sdk.response = _Response({
        "cameo_code": "195", "actor1": "United States", "actor2": "Iran",
        "confidence": 0.9, "reasoning": "Airstrikes on named sites.",
    })
    result = event_typing.code_text_event("US aircraft struck three sites.", api_key="k")
    assert result["cameo_code"] == "195"
    assert result["quad_class"] == event_typing.quad_class_for("195")
    assert result["goldstein"] == event_typing.goldstein_for("195") == -10.0
    assert result["confidence"] == 0.9


def test_a_model_supplied_quad_class_is_ignored_entirely():
    # The schema does not ask for one — asking would create a second source of
    # truth that could contradict the code.
    assert "quad_class" not in event_typing._CODING_SCHEMA["properties"]
    assert "goldstein" not in event_typing._CODING_SCHEMA["properties"]


def test_an_unscorable_code_is_refused_not_coerced(fake_sdk):
    fake_sdk.response = _Response({
        "cameo_code": "999", "actor1": "A", "actor2": "B",
        "confidence": 0.4, "reasoning": "Unclear.",
    })
    with pytest.raises(event_typing.ClassifierError, match="cameo_goldstein"):
        event_typing.code_text_event("Something happened.", api_key="k")


def test_a_content_filter_is_reported_before_content_is_read(fake_sdk):
    # A filtered call returns 200 with empty content; reading content first
    # would raise something that blames the wrong thing.
    fake_sdk.response = _Response(None, finish_reason="content_filter")
    with pytest.raises(event_typing.ClassifierError, match="declined"):
        event_typing.code_text_event("...", api_key="k")


def test_the_request_carries_the_current_model_and_a_closed_schema(fake_sdk):
    fake_sdk.response = _Response({
        "cameo_code": "190", "actor1": "A", "actor2": "B",
        "confidence": 0.8, "reasoning": "Force used.",
    })
    event_typing.code_text_event("Troops crossed the border.", api_key="k")
    kwargs = fake_sdk.last_kwargs
    assert kwargs["model"] == event_typing.CODER_MODEL == "gpt-4.1"
    schema = kwargs["response_format"]["json_schema"]
    assert kwargs["response_format"]["type"] == "json_schema"
    assert schema["strict"] is True
    assert schema["schema"] is event_typing._CODING_SCHEMA
    # No sampling parameters — they are not part of this coding path.
    assert not {"temperature", "top_p", "top_k"} & set(kwargs)


def test_the_prompt_offers_only_codes_the_archive_can_score():
    # Every code the coder is allowed to pick must already resolve, or Head A
    # could hand Head B an event with no Goldstein value.
    instructions = event_typing._instructions()
    for code in event_typing._codebook()["codes"]:
        assert f"  {code} — " in instructions
        assert event_typing.goldstein_for(code) is not None
