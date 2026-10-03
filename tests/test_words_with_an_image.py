"""Words sent with an image reach the API beside it, instead of being dropped.

Dropped, they would vanish in silence: a reader asking for "like this one but at night"
would get "like this one", with nothing to say so — neither to them nor to the model,
which would present the results as shaped by words that never reached the engine.

The API has always taken both: `text_alpha` sets their balance and defaults to 1.7, the
balance the API measured. The server never sends it, precisely so that default applies.
"""
import httpx
import pytest

from pexafy_mcp import server


class _Recorder:
    """Capture the outgoing request without sending it."""

    def __init__(self):
        self.seen = {}

    async def post(self, url, *, files=None, params=None, **kw):
        self.seen = {"url": url, "params": dict(params or {}), "files": files}
        return httpx.Response(
            200, json={"data": [], "pagination": {}},
            request=httpx.Request("POST", "http://test" + url),
        )

    async def get(self, url, **kw):
        return httpx.Response(
            200, json={"data": [], "pagination": {}},
            request=httpx.Request("GET", "http://test" + url),
        )


@pytest.mark.anyio
async def test_words_sent_with_an_image_reach_the_api(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(server, "client", rec)
    monkeypatch.setattr(server, "_fetch_image",
                        lambda url: _fake_image())
    await server.search_photos_by_image(
        image_url="https://example.test/a.jpg",
        english_search_sentence="at night",
    )
    assert rec.seen["params"].get("q") == "at night", rec.seen["params"]


@pytest.mark.anyio
async def test_text_alpha_is_never_sent(monkeypatch):
    """Not sending it is what lets the API apply its own balance."""
    rec = _Recorder()
    monkeypatch.setattr(server, "client", rec)
    monkeypatch.setattr(server, "_fetch_image", lambda url: _fake_image())
    await server.search_photos_by_image(
        image_url="https://example.test/a.jpg",
        english_search_sentence="at night",
    )
    assert "text_alpha" not in rec.seen["params"]


async def _fake_image():
    return b"\xff\xd8\xff", "image/jpeg", "a.jpg"


@pytest.fixture
def anyio_backend():
    return "asyncio"
