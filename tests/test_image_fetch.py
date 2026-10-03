"""The image URL of the by-image search is fetched by this server, so it must not be a
door into this server's own network.

Offline: the resolver is replaced by a table and every HTTP exchange is answered by an
httpx.MockTransport, so nothing here opens a socket or asks a real DNS server.
"""
from __future__ import annotations

import asyncio
import socket
import time

import httpx
import pytest
from fastmcp.exceptions import ToolError

from pexafy_mcp import server

JPEG = b"\xff\xd8\xff" + b"x" * 64
PUBLIC_IP = "93.184.215.14"
UNREACHABLE = "Could not download that image URL."
NOT_AN_IMAGE = "That URL did not return an image."

_RealAsyncClient = httpx.AsyncClient


@pytest.fixture(autouse=True)
def dns(monkeypatch):
    """A resolver that knows only this table; literal addresses answer for themselves.

    The settings a local .env may change are pinned to their defaults as well."""
    monkeypatch.setattr(server, "_IMG_ALLOW_PRIVATE", False)
    monkeypatch.setattr(server, "_IMG_FETCH_TIMEOUT", 15.0)
    monkeypatch.setattr(server, "_MAX_IMG_BYTES", server._API_IMAGE_MAX_BYTES)
    table: dict[str, list[str]] = {"images.example.com": [PUBLIC_IP]}

    async def resolve(host, port):
        if host in table:
            return list(table[host])
        try:
            import ipaddress
            return [str(ipaddress.ip_address(host))]
        except ValueError:
            raise socket.gaierror(socket.EAI_NONAME, "unknown host") from None

    monkeypatch.setattr(server, "_resolve_host", resolve)
    return table


def _serve(monkeypatch, handler):
    """Answer every request of the image fetch with *handler*; record what was asked."""
    seen: list[httpx.Request] = []

    async def recording(request):
        seen.append(request)
        response = handler(request)
        if asyncio.iscoroutine(response):
            response = await response
        return response

    class _Client(_RealAsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(recording)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(server.httpx, "AsyncClient", _Client)
    return seen


def _image(request):
    return httpx.Response(200, headers={"content-type": "image/jpeg"}, content=JPEG)


# ── Where it may go ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("address", [
    "127.0.0.1", "10.1.2.3", "172.16.0.1", "192.168.1.1", "169.254.169.254",
    "100.100.1.1", "0.0.0.0", "224.0.0.1", "::1", "fe80::1", "fc00::1", "fec0::1",
    "::ffff:127.0.0.1", "::ffff:10.0.0.1",
])
async def test_a_name_that_resolves_inside_is_refused_before_any_request(monkeypatch, dns, address):
    dns["evil.example"] = [address]
    seen = _serve(monkeypatch, _image)
    with pytest.raises(ToolError) as caught:
        await server._fetch_image("https://evil.example/cat.jpg")
    assert str(caught.value) == UNREACHABLE
    assert seen == []


async def test_one_inside_address_among_public_ones_is_enough_to_refuse(monkeypatch, dns):
    dns["mixed.example"] = [PUBLIC_IP, "10.0.0.7"]
    seen = _serve(monkeypatch, _image)
    with pytest.raises(ToolError):
        await server._fetch_image("https://mixed.example/cat.jpg")
    assert seen == []


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8080/internal", "http://[::1]/x.jpg", "http://169.254.169.254/latest/",
    "http://0.0.0.0/x.jpg", "http://[::ffff:127.0.0.1]/x.jpg",
])
async def test_a_literal_inside_address_is_refused(monkeypatch, url):
    seen = _serve(monkeypatch, _image)
    with pytest.raises(ToolError) as caught:
        await server._fetch_image(url)
    assert str(caught.value) == UNREACHABLE
    assert seen == []


async def test_the_connection_goes_to_the_address_that_was_checked(monkeypatch):
    """Pinned: a second DNS answer (rebinding) cannot change where the bytes come from.
    The name still travels in Host and in the TLS handshake."""
    seen = _serve(monkeypatch, _image)
    data, ctype, name = await server._fetch_image("https://images.example.com/a/cat.jpg?w=5")
    assert (data, ctype, name) == (JPEG, "image/jpeg", "cat.jpg")
    [request] = seen
    assert request.url.host == PUBLIC_IP
    assert request.url.path == "/a/cat.jpg" and request.url.query == b"w=5"
    assert request.headers["host"] == "images.example.com"
    assert request.extensions.get("sni_hostname") == "images.example.com"


async def test_a_redirect_is_checked_again_before_it_is_followed(monkeypatch):
    def handler(request):
        if request.headers["host"] == "images.example.com":
            return httpx.Response(302, headers={"location": "http://127.0.0.1:6379/"})
        return _image(request)

    seen = _serve(monkeypatch, handler)
    with pytest.raises(ToolError) as caught:
        await server._fetch_image("https://images.example.com/cat.jpg")
    assert str(caught.value) == UNREACHABLE
    assert [r.headers["host"] for r in seen] == ["images.example.com"]


async def test_public_redirects_are_followed_up_to_three(monkeypatch, dns):
    dns["cdn.example.org"] = ["151.101.1.1"]

    def handler(request):
        hop = int(request.url.params.get("hop", "0"))
        if hop < 3:
            return httpx.Response(302, headers={"location": f"https://cdn.example.org/c.jpg?hop={hop + 1}"})
        return _image(request)

    seen = _serve(monkeypatch, handler)
    data, _, _ = await server._fetch_image("https://images.example.com/c.jpg")
    assert data == JPEG
    assert len(seen) == 4
    assert seen[-1].url.host == "151.101.1.1"
    assert seen[-1].headers["host"] == "cdn.example.org"


async def test_a_fourth_redirect_is_one_too_many(monkeypatch):
    def handler(request):
        return httpx.Response(302, headers={"location": "/again"})

    seen = _serve(monkeypatch, handler)
    with pytest.raises(ToolError) as caught:
        await server._fetch_image("https://images.example.com/c.jpg")
    assert str(caught.value) == UNREACHABLE
    assert len(seen) == 4


async def test_a_scheme_other_than_http_is_refused_even_after_a_redirect(monkeypatch):
    seen = _serve(monkeypatch, lambda r: httpx.Response(302, headers={"location": "file:///etc/passwd"}))
    with pytest.raises(ToolError):
        await server._fetch_image("https://images.example.com/c.jpg")
    assert len(seen) == 1


# ── What it says ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("status", [401, 403, 404, 500, 503])
async def test_a_failed_download_does_not_say_what_came_back(monkeypatch, status):
    _serve(monkeypatch, lambda r: httpx.Response(status, text="internal detail"))
    with pytest.raises(ToolError) as caught:
        await server._fetch_image("https://images.example.com/c.jpg")
    assert str(caught.value) == UNREACHABLE


async def test_a_page_that_is_not_an_image_does_not_say_what_it_was(monkeypatch):
    _serve(monkeypatch, lambda r: httpx.Response(
        200, headers={"content-type": "application/json"}, content=b'{"redis": "ok"}'))
    with pytest.raises(ToolError) as caught:
        await server._fetch_image("https://images.example.com/c.jpg")
    assert str(caught.value) == NOT_AN_IMAGE


@pytest.mark.parametrize("declared", ["application/octet-stream", "binary/octet-stream",
                                      "Application/Octet-Stream; charset=binary", None])
async def test_an_image_served_without_an_image_type_is_told_by_its_bytes(monkeypatch, declared):
    """S3, Drive and many CDNs serve an upload as bytes with no type of their own. The
    first bytes decide, as they do for a type that says `image/…`."""
    headers = {"content-type": declared} if declared else {}
    _serve(monkeypatch, lambda r: httpx.Response(200, headers=headers, content=JPEG))
    data, ctype, _ = await server._fetch_image("https://images.example.com/upload")
    assert (data, ctype) == (JPEG, "image/jpeg")


async def test_untyped_bytes_that_are_not_an_image_are_still_refused(monkeypatch):
    zipped = b"PK\x03\x04" + b"\x00" * 64
    _serve(monkeypatch, lambda r: httpx.Response(
        200, headers={"content-type": "application/octet-stream"}, content=zipped))
    with pytest.raises(ToolError) as caught:
        await server._fetch_image("https://images.example.com/upload")
    assert str(caught.value) == server._IMG_BAD_URL


async def test_an_unknown_host_says_the_same_as_a_refused_one(monkeypatch):
    seen = _serve(monkeypatch, _image)
    with pytest.raises(ToolError) as caught:
        await server._fetch_image("https://nowhere.example/c.jpg")
    assert str(caught.value) == UNREACHABLE
    assert seen == []


# ── How much it reads, and for how long ──────────────────────────────────────

class _Chunks(httpx.AsyncByteStream):
    """A JPEG body, `count` chunks of `size` bytes: what starts like one is read on."""

    def __init__(self, count, size, delay=0.0):
        self.count, self.size, self.delay = count, size, delay
        self.sent = 0

    async def __aiter__(self):
        for n in range(self.count):
            if self.delay:
                await asyncio.sleep(self.delay)
            self.sent += 1
            head = b"\xff\xd8\xff" if n == 0 else b""
            yield head + b"\xff" * (self.size - len(head))


async def test_a_declared_length_over_the_cap_is_refused_unread(monkeypatch):
    monkeypatch.setattr(server, "_MAX_IMG_BYTES", 1000)
    body = _Chunks(10, 200)
    _serve(monkeypatch, lambda r: httpx.Response(
        200, headers={"content-type": "image/jpeg", "content-length": "2000"}, stream=body))
    with pytest.raises(ToolError) as caught:
        await server._fetch_image("https://images.example.com/big.jpg")
    assert "too large" in str(caught.value)
    assert body.sent == 0


async def test_an_undeclared_body_stops_being_read_at_the_cap(monkeypatch):
    monkeypatch.setattr(server, "_MAX_IMG_BYTES", 1000)
    body = _Chunks(100, 100)
    _serve(monkeypatch, lambda r: httpx.Response(
        200, headers={"content-type": "image/jpeg"}, stream=body))
    with pytest.raises(ToolError) as caught:
        await server._fetch_image("https://images.example.com/big.jpg")
    assert "too large" in str(caught.value)
    assert body.sent <= 11


async def test_one_deadline_covers_the_whole_download(monkeypatch):
    """httpx's timeout restarts at every read: a body that trickles never trips it."""
    monkeypatch.setattr(server, "_IMG_FETCH_TIMEOUT", 0.3)
    body = _Chunks(40, 10, delay=0.05)
    _serve(monkeypatch, lambda r: httpx.Response(
        200, headers={"content-type": "image/jpeg"}, stream=body))
    started = time.monotonic()
    with pytest.raises(ToolError) as caught:
        await server._fetch_image("https://images.example.com/slow.jpg")
    assert str(caught.value) == UNREACHABLE
    assert time.monotonic() - started < 1.5
    assert body.sent < 40


# ── What keeps working ───────────────────────────────────────────────────────

async def test_the_escape_hatch_lets_a_local_run_fetch_from_localhost(monkeypatch):
    monkeypatch.setattr(server, "_IMG_ALLOW_PRIVATE", True)
    seen = _serve(monkeypatch, _image)
    data, _, _ = await server._fetch_image("http://127.0.0.1:8000/test.jpg")
    assert data == JPEG
    assert seen[0].url.host == "127.0.0.1"


async def test_an_upload_from_chatgpt_is_still_fetched_and_posted(monkeypatch, dns):
    """`image_file.download_url` is a public link the host signs; it goes through the
    same checks and still reaches the API as the uploaded image."""
    dns["files.oaiusercontent.com"] = ["20.60.1.2"]
    seen = _serve(monkeypatch, _image)
    posted = {}

    class _Api:
        async def post(self, url, *, files=None, params=None, **kw):
            posted.update(url=url, files=files, params=dict(params or {}))
            return httpx.Response(200, json={"success": True, "data": []},
                                  request=httpx.Request("POST", "http://api" + url))

    monkeypatch.setattr(server, "client", _Api())
    await server.search_photos_by_image(image_file={
        "download_url": "https://files.oaiusercontent.com/file-abc?se=2026&sig=x",
        "file_id": "file-abc"})
    assert seen[0].url.host == "20.60.1.2"
    assert seen[0].url.query == b"se=2026&sig=x"
    assert posted["files"]["image"][1] == JPEG
    assert posted["files"]["image"][2] == "image/jpeg"


# ── What the API takes: its size and its four formats ────────────────────────
# The API refuses a request over 10 MiB (MAX_BODY_SIZE, src/apis/config.py) — the WHOLE
# multipart body, image and framing — and an image in any format but JPEG, PNG, WebP or
# AVIF, by their first bytes (_validate_image_bytes). The same limits here refuse such an
# image before it is downloaded in full or sent.

GIF = b"GIF89a" + b"\x00" * 64
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 32
AVIF = b"\x00\x00\x00\x20ftypavif" + b"\x00" * 32
API_MAX_BODY = 10 * 1024 * 1024        # MAX_BODY_SIZE, src/apis/config.py


def test_the_default_cap_leaves_room_for_the_multipart_framing(reload_with_env):
    reloaded = reload_with_env(server, {})
    assert reloaded._API_BODY_MAX_BYTES == API_MAX_BODY
    assert reloaded._API_IMAGE_MAX_BYTES == API_MAX_BODY - 16 * 1024
    assert reloaded._MAX_IMG_BYTES == reloaded._API_IMAGE_MAX_BYTES
    assert reloaded._img_too_large() == "That image is too large (max 10 MB)."


def _api_that_caps_the_body(fake_api):
    """The API's MaxBodySizeMiddleware: a 413 past MAX_BODY_SIZE on Content-Length."""
    bodies: list[httpx.Request] = []

    def respond(request):
        bodies.append(request)
        if int(request.headers["content-length"]) > API_MAX_BODY:
            return httpx.Response(413, json={"success": False, "error": {
                "code": "PAYLOAD_TOO_LARGE", "message": "Request body too large (max 10 MB)"}})
        return httpx.Response(200, json={"success": True, "data": []})

    fake_api.respond = respond
    return bodies


async def test_an_image_at_the_cap_fits_the_api_s_body_limit(fake_api):
    """The cap is the image's, the API's is the request's: an image the tool accepts must
    not come back as a 413 because of the few hundred bytes around it."""
    import base64

    bodies = _api_that_caps_the_body(fake_api)
    at_cap = JPEG[:3] + b"\x00" * (server._MAX_IMG_BYTES - 3)
    await server.search_photos_by_image(image_base64=base64.b64encode(at_cap).decode())
    assert int(bodies[-1].headers["content-length"]) <= API_MAX_BODY
    # One byte more is this server's refusal, before anything is sent.
    with pytest.raises(ToolError) as caught:
        await server.search_photos_by_image(image_base64=base64.b64encode(at_cap + b"\x00").decode())
    assert str(caught.value) == server._img_too_large()
    assert len(bodies) == 1


async def test_a_long_file_name_cannot_push_the_body_over(monkeypatch, fake_api):
    """The name is the part of the framing the caller writes: a URL's last segment of
    thousands of characters is cut before it goes."""
    bodies = _api_that_caps_the_body(fake_api)
    at_cap = JPEG[:3] + b"\x00" * (server._MAX_IMG_BYTES - 3)
    _serve(monkeypatch, lambda r: httpx.Response(
        200, headers={"content-type": "image/jpeg"}, content=at_cap))
    await server.search_photos_by_image(
        image_url="https://images.example.com/" + "n" * 5000 + ".jpg")
    [sent] = bodies
    assert int(sent.headers["content-length"]) <= API_MAX_BODY
    head = sent.read()[:600].decode("latin-1")
    assert 'filename="' + "n" * server._UPLOAD_NAME_MAX + '"' in head


@pytest.mark.parametrize(("cap", "said"), [
    (10 * 1024 * 1024, "max 10 MB"), (5 * 1024 * 1024, "max 5 MB"),
    (int(7.5 * 1024 * 1024), "max 7.5 MB"), (900 * 1024, "max 900 KB"),
])
def test_the_refusal_names_the_cap_in_force(monkeypatch, cap, said):
    """One figure: the one checked is the one said, whatever the setting."""
    monkeypatch.setattr(server, "_MAX_IMG_BYTES", cap)
    assert said in server._img_too_large()


@pytest.mark.parametrize(("data", "kind"), [
    (JPEG, "image/jpeg"), (b"\x89PNG\r\n\x1a\n" + b"\x00" * 8, "image/png"),
    (WEBP, "image/webp"), (AVIF, "image/avif"),
    (GIF, None), (b"<svg xmlns='http://www.w3.org/2000/svg'/>", None),
    (b"RIFF\x24\x00\x00\x00WAVEfmt ", None), (b"", None),
])
def test_the_four_formats_are_told_by_their_first_bytes(data, kind):
    assert server._image_type(data) == kind


class _Gif(httpx.AsyncByteStream):
    """A GIF, 50 chunks of 100 bytes, counting what was read."""

    def __init__(self):
        self.sent = 0

    async def __aiter__(self):
        for n in range(50):
            self.sent += 1
            yield (GIF[:12] + b"\x00" * 88) if n == 0 else b"\x00" * 100


async def test_a_gif_is_refused_after_its_first_bytes(monkeypatch):
    stream = _Gif()
    _serve(monkeypatch, lambda r: httpx.Response(
        200, headers={"content-type": "image/gif"}, stream=stream))
    with pytest.raises(ToolError) as caught:
        await server._fetch_image("https://images.example.com/anim.gif")
    assert str(caught.value) == server._IMG_BAD_URL
    assert "JPEG, PNG, WebP or AVIF" in str(caught.value)
    assert stream.sent <= 2, "the rest of the file was downloaded"


async def test_the_format_sent_on_is_the_one_the_bytes_are(monkeypatch):
    """A header that says `image/jpg`, or nothing precise, does not travel: the type the
    API is told is read off the bytes."""
    _serve(monkeypatch, lambda r: httpx.Response(
        200, headers={"content-type": "image/jpg"}, content=JPEG))
    _, ctype, _ = await server._fetch_image("https://images.example.com/a.jpg")
    assert ctype == "image/jpeg"


def test_bytes_in_another_format_are_refused_before_they_are_sent():
    import base64

    with pytest.raises(ToolError) as caught:
        server._decode_base64_image(base64.b64encode(GIF).decode())
    assert str(caught.value) == server._IMG_B64_FORMAT
    for data, kind in ((WEBP, "image/webp"), (AVIF, "image/avif")):
        assert server._decode_base64_image(base64.b64encode(data).decode())[1] == kind


async def test_an_unsupported_image_never_reaches_the_api(monkeypatch):
    import base64

    posted = []

    class _Api:
        async def post(self, url, **kw):
            posted.append(url)
            raise AssertionError("the API was called")

    monkeypatch.setattr(server, "client", _Api())
    with pytest.raises(ToolError):
        await server.search_photos_by_image(image_base64=base64.b64encode(GIF).decode())
    assert posted == []
