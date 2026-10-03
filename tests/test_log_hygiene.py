"""What this server's log never holds in clear: a search sentence, an image, a token, an
e-mail, an address.

Each test drives one path that used to write, or could write, one of them: the guards
quoting the argument they refused, FastMCP quoting a call that failed validation,
httpx writing the URL of every outgoing request (the sentence in the API's query
string, the signed link of an uploaded image), uvicorn opening every access line with
the caller's address and ending its request line with the query string, the OAuth path
naming the user by e-mail.

    pytest tests/test_log_hygiene.py -v
"""
from __future__ import annotations

import io
import logging
import logging.config

import httpx
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from pexafy_mcp import guards, observe, server, tooling

SECRET = "SECRET-TEXT a beach at dawn"


class _Grab(logging.Handler):
    """Every record a logger creates, formatted — attached to the logger itself, since
    FastMCP's loggers do not all reach the root one."""

    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(record.getMessage())


@pytest.fixture
def grab():
    handlers = []

    def attach(name: str) -> _Grab:
        handler = _Grab()
        logger = logging.getLogger(name)
        handlers.append((logger, handler, logger.level))
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        return handler

    yield attach
    for logger, handler, level in handlers:
        logger.removeHandler(handler)
        logger.setLevel(level)


@pytest.mark.parametrize(("value", "kind"), [
    (None, "nothing"), ("   ", "blank"), (["a", "b"], "list"), (7, "int"),
    ("#6", "a rank"), ("https://example.com/me.jpg", "a URL"),
    # A single word is still somebody's: a first name sent as a photo_id, a one-word
    # query. Only a shape this server names itself is shown.
    ("Marouane", "a word (8 letters)"), ("vertical", "a word (8 letters)"),
    ("landscape", "'landscape'"), (" square ", "'square'"),
    (SECRET, f"{len(SECRET)} characters"),
])
def test_a_refused_value_is_described_not_quoted(value, kind):
    assert guards._described(value) == kind


async def test_a_single_word_is_not_quoted_either(grab):
    lines = grab("pexafy.mcp")
    async with Client(server.build_server()) as client:
        for tool, arguments in (
            (tooling.PUBLIC_TOOL_NAMES["search_photos_by_image"], {"photo_id": "Marouane"}),
            (tooling.PUBLIC_TOOL_NAMES["search_photos"],
             {"english_search_sentence": "cats", "explicit_orientation_filter": ["Juliette"]}),
        ):
            with pytest.raises(ToolError):
                await client.call_tool(tool, arguments)
    refused = [line for line in lines.lines if line.startswith("Rejected")]
    assert len(refused) == 2, lines.lines
    assert not any("Marouane" in line or "Juliette" in line for line in refused), refused
    assert all("a word (8 letters)" in line for line in refused), refused


async def test_the_guards_log_what_they_refused_without_quoting_it(grab):
    lines = grab("pexafy.mcp")
    async with Client(server.build_server()) as client:
        for tool, arguments in (
            (tooling.PUBLIC_TOOL_NAMES["search_photos"], {"english_search_sentence": [SECRET]}),
            (tooling.PUBLIC_TOOL_NAMES["search_photos_by_image"], {"photo_id": SECRET}),
            (tooling.PUBLIC_TOOL_NAMES["search_photos_by_image"],
             {"photo_id": "https://example.com/SECRET-TEXT.jpg"}),
            (tooling.PUBLIC_TOOL_NAMES["search_photos"],
             {"english_search_sentence": "cats", "explicit_orientation_filter": [SECRET]}),
        ):
            with pytest.raises(ToolError):
                await client.call_tool(tool, arguments)
    refused = [line for line in lines.lines if line.startswith("Rejected")]
    assert len(refused) == 4, lines.lines
    assert not any("SECRET" in line for line in refused), refused
    assert any("a URL" in line for line in refused)


async def test_fastmcp_s_warning_about_a_refused_call_does_not_quote_it(grab):
    """FastMCP logs pydantic's error list, and each error carries the `input` it
    refused: a sentence, a base64 image."""
    lines = grab(observe.FASTMCP_CALL_LOGGER)
    async with Client(server.build_server()) as client:
        for arguments in ({"image_url": "https://example.com/x.jpg", "english_search_sentence": [SECRET]},
                          {"image_base64": [SECRET]}):
            with pytest.raises(ToolError):
                await client.call_tool(tooling.PUBLIC_TOOL_NAMES["search_photos_by_image"], arguments)
    warned = [line for line in lines.lines if line.startswith("Invalid arguments")]
    assert len(warned) == 2, lines.lines
    assert not any("SECRET" in line for line in warned), warned
    # What stays is what a reader of the log needs: the tool, the field, the problem.
    assert "english_search_sentence" in warned[0] and "string" in warned[0]


def test_the_filter_also_cuts_pydantic_s_own_rendering():
    record = logging.LogRecord(
        observe.FASTMCP_CALL_LOGGER, logging.WARNING, __file__, 1,
        observe.WithoutArgumentValues.MESSAGE,
        ("t", "1 validation error for call[t]\nq\n  Input should be a valid string "
              f"[type=string_type, input_value={SECRET!r}, input_type=list]"),
        None,
    )
    assert observe.WithoutArgumentValues().filter(record) is True
    assert record.getMessage() == "Invalid arguments for tool 't': 1 validation error for call[t]"


def test_the_filter_is_installed_once():
    observe.install_log_filters()
    observe.install_log_filters()
    installed = [f for f in logging.getLogger(observe.FASTMCP_CALL_LOGGER).filters
                 if isinstance(f, observe.WithoutArgumentValues)]
    assert len(installed) == 1


def test_the_access_log_carries_no_address():
    """uvicorn opens every access line with `client_addr` — the person's own address
    once it trusts the proxy's X-Forwarded-For."""
    config = server.uvicorn_log_config()
    fmt = config["formatters"]["access"]["fmt"]
    assert "client_addr" not in fmt
    assert "request_line" in fmt and "status_code" in fmt
    # uvicorn's own default is left as it was.
    from uvicorn.config import LOGGING_CONFIG
    assert "client_addr" in LOGGING_CONFIG["formatters"]["access"]["fmt"]


# ── What the libraries write on their own ────────────────────────────────────
# httpx writes every outgoing URL at INFO, the level production runs at: the sentence of
# a text search sits in the API's query string, and the URL of a reference image is the
# image — a ChatGPT upload link carries its access in `?se=…&sig=…`. The calls go through
# a real in-memory client, and every record is read: the root's (httpx, mcp and this
# server propagate there) and FastMCP's, which does not propagate. At DEBUG too, where
# the SDK would dump every message it receives.

SENTENCE = "my daughter Alice at Lycee Victor Hugo"
SAS = "se=2026-09-27&sig=SAS-SECRET-TOKEN"
IMAGE_URL = f"https://93.184.215.14/file-abc/upload.jpg?{SAS}"
UPLOAD_URL = f"https://20.60.1.2/files/file-xyz/photo.jpg?{SAS}"
JPEG = b"\xff\xd8\xff" + b"x" * 64


class _Everything(logging.Handler):
    """Every record, rendered whole — logger name, level, message and any traceback."""

    def __init__(self):
        super().__init__(logging.DEBUG)
        self.setFormatter(logging.Formatter("%(name)s %(levelname)s %(message)s"))
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(self.format(record))


@pytest.fixture
def everything():
    """Capture every logger at a given root level, with the libraries' levels as
    `install_log_filters` leaves them over that root. All put back afterwards."""
    names = ["", "fastmcp", "httpx", "httpcore", "mcp", "sse_starlette", "uvicorn"]
    loggers = [logging.getLogger(name) for name in names]
    saved = [(logger, logger.level) for logger in loggers]
    handler = _Everything()

    def start(level: int) -> _Everything:
        logging.getLogger().setLevel(level)                # PEXAFY_MCP_LOG_LEVEL
        for name in observe.LIBRARY_LOG_FLOORS:
            logging.getLogger(name).setLevel(logging.NOTSET)
        logging.getLogger("fastmcp").setLevel(level)       # FASTMCP_LOG_LEVEL, the same
        observe.install_log_filters()                      # what server.py does at import
        for logger in loggers:
            logger.addHandler(handler)
        return handler

    yield start
    for logger in loggers:
        logger.removeHandler(handler)
    for logger, level in saved:
        logger.setLevel(level)


def _image_host():
    """httpx.AsyncClient as the image download builds it, answered in memory."""
    real = httpx.AsyncClient

    class _ImageHost(real):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(lambda request: httpx.Response(
                200, headers={"content-type": "image/jpeg"}, content=JPEG))
            super().__init__(*args, **kwargs)

    return _ImageHost


@pytest.mark.parametrize("level", [logging.INFO, logging.DEBUG], ids=["INFO", "DEBUG"])
async def test_no_library_writes_what_a_request_said(everything, fake_api, monkeypatch, level):
    lines = everything(level)
    async with Client(server.build_server()) as client:
        await client.call_tool(tooling.PUBLIC_TOOL_NAMES["search_photos"],
                               {"english_search_sentence": SENTENCE})
        monkeypatch.setattr(server.httpx, "AsyncClient", _image_host())
        by_image = tooling.PUBLIC_TOOL_NAMES["search_photos_by_image"]
        await client.call_tool(by_image, {"image_url": IMAGE_URL,
                                          "english_search_sentence": "Alice at night"})
        await client.call_tool(by_image, {"image_file": {"download_url": UPLOAD_URL,
                                                         "file_id": "file-xyz"}})
    assert len(fake_api.requests) == 3                     # the three calls did run
    text = "\n".join(lines.lines)
    for said in ("Alice", "Victor", "Hugo", "SAS-SECRET-TOKEN", "sig=", "se=2026",
                 "file-abc", "file-xyz", "upload.jpg", "photo.jpg"):
        assert said not in text, (said, [line for line in lines.lines if said in line])
    # What replaces httpx's line keeps the count, and nothing a person wrote.
    assert "pexafy.mcp INFO API GET /api/v1/search/photos 200" in text
    assert text.count("API POST /api/v1/search/photos 200") == 2


def test_the_libraries_are_held_whatever_the_root_says(everything):
    everything(logging.DEBUG)
    for name, floor in observe.LIBRARY_LOG_FLOORS.items():
        assert logging.getLogger(name).getEffectiveLevel() >= floor, name
    # This server's own lines still follow the setting.
    assert logging.getLogger("pexafy.mcp").getEffectiveLevel() == logging.DEBUG


def test_a_quieter_setting_stays_quieter(everything):
    """The floor only ever raises a level: at WARNING the SDK's INFO lines stay off."""
    everything(logging.WARNING)
    assert logging.getLogger("mcp").getEffectiveLevel() == logging.WARNING
    assert logging.getLogger("httpx").getEffectiveLevel() == logging.WARNING


# ── uvicorn's access line ────────────────────────────────────────────────────

@pytest.fixture
def uvicorn_loggers():
    """uvicorn's loggers as dictConfig finds and leaves them, put back afterwards."""
    names = ("uvicorn", "uvicorn.error", "uvicorn.access")
    saved = {}
    for name in names:
        logger = logging.getLogger(name)
        saved[name] = (list(logger.handlers), logger.level, logger.propagate,
                       list(logger.filters), logger.disabled)
    yield
    for name, (handlers, level, propagate, filters, disabled) in saved.items():
        logger = logging.getLogger(name)
        logger.handlers[:] = handlers
        logger.setLevel(level)
        logger.propagate = propagate
        logger.filters[:] = filters
        logger.disabled = disabled


def test_the_access_line_carries_no_address_and_no_query(uvicorn_loggers):
    """Through the configuration main() hands uvicorn, and uvicorn's own call: the filter
    must survive dictConfig, and the formatter must not bring the address back."""
    observe.install_log_filters()
    logging.config.dictConfig(server.uvicorn_log_config())
    access = logging.getLogger(observe.ACCESS_LOGGER)
    out = io.StringIO()
    access.handlers[0].stream = out
    access.info('%s - "%s %s HTTP/%s" %d', "203.0.113.42:5555", "POST",
                "/mcp?api_key=pexafy_SECRETKEY", "1.1", 200)
    access.info('%s - "%s %s HTTP/%s" %d', "203.0.113.42:5555", "GET", "/health", "1.1", 200)
    line, plain = out.getvalue().splitlines()
    assert "pexafy_SECRETKEY" not in line and "api_key" not in line
    assert "203.0.113.42" not in line
    assert '"POST /mcp?… HTTP/1.1" 200' in line
    assert '"GET /health HTTP/1.1" 200' in plain


def test_main_hands_uvicorn_that_configuration(monkeypatch):
    """The configuration is only worth something if main() passes it: a `uvicorn.run`
    without `log_config` is back to uvicorn's default, address first."""
    import sys

    import uvicorn

    ran = {}
    monkeypatch.setattr(server, "TRANSPORT", "http")
    monkeypatch.setattr(sys, "argv", ["pexafy-mcp"])
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: ran.update(app=app, **kwargs))
    server.main()
    assert ran["app"] is not None
    assert ran["log_config"] == server.uvicorn_log_config()
    assert "client_addr" not in ran["log_config"]["formatters"]["access"]["fmt"]
    assert (ran["host"], ran["port"]) == (server.MCP_HOST, server.MCP_PORT)


# ── An e-mail, keyed ─────────────────────────────────────────────────────────
# A plain hash of an address is the address to anyone who holds the list of accounts:
# hash them all, read the log back. The digest the OAuth lines carry is an HMAC.

EMAIL = "jane@example.com"


def _hmac(key: bytes, email: str = EMAIL) -> str:
    import hashlib
    import hmac

    return hmac.new(key, f"pexafy-log-email:v1:{email}".encode(), hashlib.sha256).hexdigest()[:16]


def test_an_email_is_keyed_by_the_secret_shared_with_django(monkeypatch):
    monkeypatch.setenv("MCP_RESOLVE_SECRET", "resolve-k1")
    monkeypatch.setenv("PEXAFY_ANON_SECRET", "anon-k")
    assert observe.email_digest(EMAIL) == _hmac(b"resolve-k1")
    monkeypatch.setenv("MCP_RESOLVE_SECRET", "resolve-k2")
    assert observe.email_digest(EMAIL) == _hmac(b"resolve-k2")    # another key, another name


def test_the_anonymous_secret_keys_it_when_there_is_no_oauth_one(monkeypatch):
    monkeypatch.delenv("MCP_RESOLVE_SECRET", raising=False)
    monkeypatch.setenv("PEXAFY_ANON_SECRET", "anon-k")
    assert observe.email_digest(EMAIL) == _hmac(b"anon-k")


def test_with_no_secret_at_all_it_is_still_keyed(monkeypatch):
    """This process's own key: stable from one line to the next, and not a hash anyone
    can recompute."""
    import hashlib

    monkeypatch.delenv("MCP_RESOLVE_SECRET", raising=False)
    monkeypatch.delenv("PEXAFY_ANON_SECRET", raising=False)
    named = observe.email_digest(EMAIL)
    assert named == observe.email_digest(EMAIL)
    assert named != observe.email_digest("john@example.com")
    for unkeyed in (observe.digest(EMAIL), hashlib.sha256(EMAIL.encode()).hexdigest(),
                    hashlib.sha1(EMAIL.encode()).hexdigest(), _hmac(b"")):
        assert named not in unkeyed


@pytest.mark.parametrize("email", ["", None])
def test_no_email_is_a_question_mark(email):
    assert observe.email_digest(email) == "?"
