"""GDELT 2.0 15-minute fetch: a 404 on the newest zip is the publish race."""

from __future__ import annotations

import io
import urllib.error
import zipfile
from email.message import Message

from core.ingestion import stream


def _zip_bytes(text: str = "hdr\n") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("export.CSV", text)
    return buf.getvalue()


def _http_error(url: str, code: int = 404) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, code, "Not Found", Message(), io.BytesIO())


def test_walks_back_when_the_newest_export_is_404(monkeypatch):
    stream.clear_cache()
    newest = "http://data.gdeltproject.org/gdeltv2/20260821190000.export.CSV.zip"
    older = "http://data.gdeltproject.org/gdeltv2/20260821184500.export.CSV.zip"
    seen: list[str] = []

    def fake_get(url: str, *, timeout: int = 60) -> bytes:
        seen.append(url)
        if "lastupdate" in url:
            return f"1 x {newest}\n".encode()
        if url == newest:
            raise _http_error(url)
        if url == older:
            return _zip_bytes("row\n")
        raise _http_error(url)

    monkeypatch.setattr(stream, "_get", fake_get)
    url, lines, fetched, error = stream.latest_file()
    assert error is None
    assert url == older
    assert lines == ["row\n"]
    assert fetched
    assert newest in seen
    assert older in seen


def test_poll_does_not_raise_when_the_window_is_all_404(monkeypatch):
    stream.clear_cache()

    def fake_get(url: str, *, timeout: int = 60) -> bytes:
        raise _http_error(url)

    monkeypatch.setattr(stream, "_get", fake_get)

    class _Pack:
        name = "mena"
        external_powers = ()

    result = stream.poll(_Pack(), {})
    assert result["rows"] == []
    assert result["error"]
    assert "HTTP Error" not in result["error"]
    assert "404" not in result["error"] or "CDN" in result["error"]


def test_public_error_does_not_echo_urllib():
    assert stream.public_error("HTTP 404") == (
        "GDELT's newest 15-minute file is not on the CDN yet"
    )
    assert "urllib" not in (stream.public_error("HTTP Error 404: Not Found") or "")
    assert stream.public_error(None) is None
