"""The grid in hosts that are neither ChatGPT nor Claude — VS Code, Cursor — and the coach.

Measured in preprod (2026-09-28): VS Code and Cursor run the frame's tool calls
(`serverTools`) but carry no message to the chat (`ui/message`), ignore the
`_meta["mcp/www_authenticate"]` ChatGPT connects an account with, and draw nothing above
the frame. So "Search again" and "Try again" did nothing, "Sign in" did nothing, and the
name at the top left was plain text with no logo anywhere. And the coach started over in
every conversation, for everybody: the frame can remember nothing past one answer.

  * Search again / Try again: the frame runs the question behind the answer itself
    where the host takes no message (widget.searchAgain).
  * Sign in: a call of connect_account from such a host is left without the anonymous
    bearer, so it is answered 401 and the host runs its own sign-in
    (anonymous.asks_to_sign_in).
  * The name: drawn as the site's logo, for hosts known to draw none, with a rule the
    page ChatGPT reads never holds (hosts.LogoWhereTheHostDrawsNone).
  * The coach: the server says `coach: false` past a few searches (budget.account).
"""
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from unittest.mock import patch

import pytest

from pexafy_mcp import (anonymous, budget, hosts, linking, previews, server, store,
                        tooling, widget)

JS = widget._with_tool_names(widget._WIDGET_JS)
# English only: these run the grid as an English reader sees it, and the whole table
# would put the script past the length of one command-line argument.
I18N_JS = widget._I18N_JS.replace("__I18N__", '{"k": [], "t": {}}')
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _function(signature: str) -> str:
    i = JS.index(signature)
    return JS[i:JS.index("\n}\n", i) + 2]


def _call(name: str, arguments: dict | None = None, meta: dict | None = None) -> bytes:
    params = {"name": name, "arguments": arguments or {}}
    if meta is not None:
        params["_meta"] = meta
    return json.dumps({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                       "params": params}).encode()


CONNECT = tooling.PUBLIC_TOOL_NAMES["connect_account"]
VSCODE = {"user-agent": "node"}


@pytest.fixture(autouse=True)
def _clean_store():
    """The store in this process's memory, empty for each test (no PEXAFY_REDIS_URL)."""
    with patch.object(store, "URL", ""):
        store._memory.clear()
        yield
        store._memory.clear()


# ── Sign in: a 401 for the hosts that sign in on one ─────────────────────────

def test_the_tool_the_gate_watches_is_the_connect_tool():
    assert anonymous._CONNECT_TOOL == CONNECT
    assert linking.CONNECT_TOOL == "connect_account"
    assert anonymous._OPENAI_MARKERS == hosts.OPENAI_CLIENT_MARKERS
    assert anonymous._OPENAI_META_PREFIX == hosts.OPENAI_META_PREFIX


def test_asking_to_connect_from_another_host_is_a_sign_in():
    assert anonymous.asks_to_sign_in(_call(CONNECT), VSCODE)
    assert anonymous.asks_to_sign_in(_call(CONNECT, {"check_only": False}), VSCODE)


@pytest.mark.parametrize("body, headers", [
    # The grid's probe never raises, and never gets a 401.
    (_call(CONNECT, {"check_only": True}), VSCODE),
    # ChatGPT keeps its own flow, however it is told.
    (_call(CONNECT, meta={"openai/subject": "s"}), VSCODE),
    (_call(CONNECT), {"user-agent": "openai-mcp/1.0.0"}),
    (_call(CONNECT), {anonymous.OPENAI_SUBJECT_HEADER: "s"}),
    # Anything else is not a sign-in.
    (_call("search_photos", {"english_search_sentence": "a red bicycle"}), VSCODE),
    (json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode(), VSCODE),
    (b"not json", VSCODE),
    (b"", VSCODE),
    (None, VSCODE),
])
def test_what_is_not_a_sign_in(body, headers):
    assert not anonymous.asks_to_sign_in(body, headers)


def _through(body: bytes, *, sign_in_by_401=True, chunk: int = 0, headers=None,
             ip: str = "203.0.113.9"):
    """Run one POST through AllowAnonymous; return what the app behind it saw: whether
    it was given the anonymous bearer, and the body it read."""
    seen = {}

    async def app(scope, receive, send):
        seen["auth"] = any(k == b"authorization" for k, _ in scope["headers"])
        if scope.get(anonymous.SIGN_IN_SCOPE_KEY):
            seen["sign_in"] = scope[anonymous.SIGN_IN_SCOPE_KEY]
        parts = []
        while True:
            message = await receive()
            parts.append(message.get("body") or b"")
            if not message.get("more_body"):
                break
        seen["body"] = b"".join(parts)

    pieces = [body[i:i + chunk] for i in range(0, len(body), chunk)] if chunk else [body]
    messages = [{"type": "http.request", "body": p, "more_body": i < len(pieces) - 1}
                for i, p in enumerate(pieces)]

    async def receive():
        return messages.pop(0)

    raw = [(b"user-agent", b"node"), (b"cf-connecting-ip", ip.encode())]
    raw += [(k.encode(), v.encode()) for k, v in (headers or {}).items()]
    scope = {"type": "http", "method": "POST", "path": "/mcp", "headers": raw}
    gate = anonymous.AllowAnonymous(app, sign_in_by_401=sign_in_by_401)
    with patch.object(anonymous, "is_configured", lambda: True), \
         patch.object(anonymous, "SOURCES", ["openai-subject", "ip"]):
        asyncio.run(gate(scope, receive, lambda message: None))
    return seen


def test_the_sign_in_call_goes_on_without_the_anonymous_bearer():
    """No bearer: FastMCP's auth answers it 401, which is what the host signs in on."""
    body = _call(CONNECT)
    seen = _through(body)
    assert seen == {"auth": False, "body": body, "sign_in": "connect"}


def test_everything_else_is_served_anonymously_with_its_body_intact():
    for body in (_call(CONNECT, {"check_only": True}), _call("search_photos"),
                 _call(CONNECT, meta={"openai/subject": "s"})):
        assert _through(body) == {"auth": True, "body": body}
    # Read in pieces, replayed whole.
    body = _call("search_photos", {"english_search_sentence": "x" * 500})
    assert _through(body, chunk=64) == {"auth": True, "body": body}
    # Past the size a sign-in could be, the reading stops and the rest still arrives.
    big = _call("search_photos_by_image", {"image_base64": "A" * (40 * 1024)})
    assert _through(big, chunk=4096) == {"auth": True, "body": big}


def test_without_oauth_nothing_is_withheld():
    """Only where the auth layer answers a bare request 401 (server.OAUTH_ENABLED)."""
    assert _through(_call(CONNECT), sign_in_by_401=False)["auth"] is True
    with patch.object(anonymous, "SIGN_IN_BY_401", False):
        assert anonymous.AllowAnonymous(None, sign_in_by_401=True).sign_in_by_401 is False
    assert "sign_in_by_401=OAUTH_ENABLED" in open(server.__file__, encoding="utf-8").read()


def _initialize(name: str) -> bytes:
    return json.dumps({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                       "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                  "clientInfo": {"name": name, "version": "1"}}}).encode()


OAUTH = {"authorization": "Bearer an-oauth-token"}


def test_the_editor_that_signed_in_is_asked_to_at_every_start():
    """Measured in VS Code: after the 401 the token comes back on the same session,
    which the SDK refuses (404), and the editor opens a new session without its token.
    That start meets a 401, the editor comes back with its token — and so on at every
    start from then on."""
    vscode = _initialize("Visual Studio Code")
    assert _through(vscode)["auth"] is True             # before: served anonymously
    assert _through(_call(CONNECT))["auth"] is False     # Sign in: 401
    for _ in range(3):
        assert _through(vscode)["auth"] is False         # a start: 401…
        assert _through(vscode, headers=OAUTH) == {"auth": True, "body": vscode}  # …token
    body = _call("search_photos")
    assert _through(body) == {"auth": True, "body": body}   # a call is not a start


def test_signed_out_it_is_anonymous_again():
    """2026-10-01: "even after I disconnect, it insists on an account". Asked at a start
    and back WITHOUT a token: no account any more, the hold is dropped."""
    vscode = _initialize("Visual Studio Code")
    _through(_call(CONNECT))
    assert _through(vscode)["auth"] is False             # asked
    assert _through(vscode, headers=OAUTH)["auth"] is True
    assert _through(vscode)["auth"] is False             # next start, asked again
    assert _through(vscode)["auth"] is True              # back without a token: let go
    assert _through(vscode)["auth"] is True              # and anonymous from then on
    assert not any(":editor:" in k or ":asked:" in k for k in store._memory)


def test_an_editor_that_starts_with_its_token_is_held():
    """Signed in some other way (an account it already held): its token-less starts are
    asked too, so the account stays attached."""
    vscode = _initialize("Visual Studio Code")
    assert _through(vscode, headers=OAUTH)["auth"] is True
    assert _through(vscode)["auth"] is False
    # A raw Pexafy key is a configuration, not a sign-in: nothing is held for it.
    store._memory.clear()
    _through(vscode, headers={"authorization": "Bearer pexafy_api_xyz"})
    assert _through(vscode)["auth"] is True


def test_the_sign_in_stays_with_that_editor_and_that_machine():
    _through(_call(CONNECT))
    _through(_initialize("Visual Studio Code"))          # takes the sign-in
    assert _through(_initialize("cursor-vscode"))["auth"] is True
    assert _through(_initialize("Visual Studio Code"), ip="198.51.100.4")["auth"] is True


def test_chatgpt_is_never_asked_at_its_start():
    _through(_call(CONNECT))
    assert _through(_initialize("openai-mcp"))["auth"] is True
    assert _through(_initialize("Visual Studio Code"),
                    headers={"user-agent": "openai-mcp/1.0.0"})["auth"] is True


def test_a_sign_in_nobody_followed_is_forgotten():
    _through(_call(CONNECT))
    for key in list(store._memory):
        store._memory[key] = 0                             # expired
    assert _through(_initialize("Visual Studio Code"))["auth"] is True


def test_the_machine_is_kept_as_a_digest():
    _through(_call(CONNECT))
    _through(_initialize("Visual Studio Code"))
    kept = " ".join(store._memory)
    assert "203.0.113.9" not in kept and "visual" not in kept.lower()
    assert all(k.startswith(store.PREFIX + "signin:") for k in store._memory)


def test_a_declined_sign_in_tells_the_model_what_to_do_next():
    """Measured in VS Code: the person declines the prompt and the 401's body becomes the
    tool's answer. It said "needs to know who you are… create an MCP Agent key", and the
    model went looking for keys and web pages. Each sign-in 401 now says what comes next."""
    from pexafy_mcp import unauthorized

    _through(_call(CONNECT))
    assert _through(_initialize("Visual Studio Code"))["sign_in"] == "start"
    for kind, words in (("start", "Call the same tool again now"),
                        ("connect", "searching works without an account")):
        body = json.loads(unauthorized._rewrite({anonymous.SIGN_IN_SCOPE_KEY: kind}, b""))
        text = body["error_description"]
        assert text.startswith("The person declined to sign in.")
        assert words in text
        # Measured: told it was "asked to sign in", the model called connect_account.
        assert "Do not call connect_account" in text
        assert "key" not in text.lower()
    # Any other 401 keeps its words.
    plain = json.loads(unauthorized._rewrite({"headers": []}, b""))
    assert "needs to know who you are" in plain["error_description"]


def test_the_frame_says_where_the_sign_in_is():
    connect = _function("async function connectAccount(url) {")
    assert 'if (!window.openai) toast(tx("Sign in from the prompt your app opens"), true);' in connect
    assert connect.index("toast(") < connect.index('callServerTool("' + CONNECT)


# ── The coach: once per person (coach.py) ───────────────────────────────────

def test_the_frame_takes_the_server_s_word_on_the_coach():
    links = _function("function learnLinks(meta) {")
    assert 'meta["pexafy/coach"]' in links
    assert "show: cm.show !== false" in links
    mute = _function("function serverMutesCoach() {")
    assert "coachMeta.show === false" in mute
    # Before the first paint, over a restored view, and kept with the view.
    render = _function("function render(sc) {")
    assert render.index("if (coachMuted()) coach.off = true;") < render.index("showStep();")
    assert "if (serverMutesCoach()) saveUi();" in render
    restore = _function("function applyView(ui, fromServer) {")
    assert "if (coachMuted()) coach.off = true;" in restore
    # The deck's sway, a coaching gesture too.
    assert "if (card && !coachMuted()) {" in JS


def test_a_step_on_screen_is_reported_once():
    paint = _function("function paintCoach() {")
    assert "if (gridOn && next) reportCoachShown();" in paint


@needs_node
def test_the_report_is_one_post_with_the_token():
    script = r"""
const posts = [];
function fetch(url, init) { posts.push([url, init.method, init.body, init.headers["Content-Type"]]);
                            return Promise.resolve(); }
let coachMeta = { show: true, url: "https://mcp.example/coach", token: "tok" };
""" + JS[JS.index("let coachReported = false;"):JS.index("function reportCoachShown() {")] \
        + _function("function reportCoachShown() {") + r"""
reportCoachShown(); reportCoachShown();
coachMeta = null; coachReported = false; reportCoachShown();
console.log(JSON.stringify(posts));
"""
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, check=True).stdout
    assert json.loads(out) == [["https://mcp.example/coach", "POST", '{"token":"tok"}',
                                "text/plain;charset=UTF-8"]]


def test_the_rejected_threshold_is_gone():
    assert not hasattr(budget, "COACH_UNTIL")
    assert "coach" not in budget.account({"X-Plan": "anonymous", "X-Daily-Quota-Limit": "100",
                                          "X-Daily-Quota-Remaining": "1"})


# ── The name, as the site's logo, where the host draws none ──────────────────

def test_the_page_everybody_gets_has_no_logo():
    """test_no_logo_and_no_custom_gradient still holds for the page as built."""
    assert widget.LOGO_CSS not in widget.GRID_HTML
    for mark in ("background-clip", "#06b6d4"):
        assert mark not in widget.GRID_HTML, mark
    decorated = widget.with_logo(widget.GRID_HTML)
    assert decorated.count(widget.LOGO_CSS) == 1
    assert decorated.index(widget.LOGO_CSS) < decorated.index("</head>")


@pytest.fixture
def previews_on(reload_with_env):
    """The grid is only registered when the thumbnail CDN is configured."""
    reload_with_env(previews, {
        "PEXAFY_THUMB_BASE_URL": "https://thumb.pexafy.com",
        "PEXAFY_THUMB_HMAC_SECRET": "test-secret",
    })


async def _page_seen_by(client_name: str | None) -> str:
    from fastmcp import Client
    from mcp.types import Implementation

    info = Implementation(name=client_name, version="1") if client_name else None
    async with Client(server.build_server(), client_info=info) as client:
        read = await client.read_resource(widget.GRID_URI)
    return read[0].text


async def test_vs_code_and_cursor_are_served_the_logo(previews_on):
    for name in ("Visual Studio Code", "cursor-vscode", "goose"):
        assert widget.LOGO_CSS in await _page_seen_by(name), name


async def test_chatgpt_claude_and_unknown_hosts_are_not(previews_on):
    # A scanner whose name is not documented reads the page ChatGPT reads.
    for name in ("openai-mcp", "OpenAI Apps Scanner", "claude-ai", "Anthropic/ClaudeAI",
                 "claude-code", "mcp-scan", "some-inspector", None):
        page = await _page_seen_by(name)
        assert widget.LOGO_CSS not in page, name
        assert "background-clip" not in page, name


async def test_the_logo_never_carries_over_to_the_next_host(previews_on):
    await _page_seen_by("Visual Studio Code")
    assert "background-clip" not in await _page_seen_by("openai-mcp")


def test_the_frame_puts_the_class_on_where_the_host_draws_no_name():
    shows = _function("function hostShowsAppIdentity() {")
    assert "if (window.openai) return true;" in shows
    assert "/claude|anthropic/.test(n)" in shows
    assert 'brandEl.classList.toggle("logo", !hostShowsAppIdentity());' in JS
    # After the handshake, where the host's name is known.
    boot = JS[JS.rindex("await app.connect();"):]
    assert "try { paintBrand(); } catch (e) {}" in boot[:400]


@needs_node
@pytest.mark.parametrize("host, logo", [
    ({"openai": True}, False),
    ({"name": "Claude"}, False),
    ({"name": "claude-ai"}, False),
    ({"name": "Visual Studio Code"}, True),
    ({"name": "cursor-vscode"}, True),
])
def test_which_hosts_get_the_logo_class(host, logo):
    script = r"""
const host = JSON.parse(require("fs").readFileSync(0, "utf8"));
const window = host.openai ? { openai: {} } : {};
const app = { getHostVersion: () => (host.name ? { name: host.name } : null) };
let toggled = null;
const brandEl = { classList: { toggle: (c, on) => { toggled = [c, on]; } } };
function surface() { return "mcp-host"; }
""" + _function("function slug(v) {") + _function("function hostShowsAppIdentity() {") \
        + _function("function paintBrand() {") + "\npaintBrand();\nconsole.log(JSON.stringify(toggled));\n"
    out = subprocess.run([NODE, "-e", script], input=json.dumps(host),
                         capture_output=True, text=True, check=True).stdout
    assert json.loads(out) == ["logo", logo]


# ── Search again, Try again ──────────────────────────────────────────────────

def test_both_buttons_go_through_search_again():
    assert JS.count("searchAgain();") == 2
    assert "if (speak(TRY_AGAIN_ASK)) toast(" not in JS.replace(
        _function("async function searchAgain() {"), "")


_AGAIN = I18N_JS + r"""
const plan = JSON.parse(require("fs").readFileSync(0, "utf8"));
const log = [];
const TRY_AGAIN_ASK = "Run my last Pexafy search again.";
let ORIGIN = plan.origin;
function speak(text) { log.push(["speak", text]); return plan.speaks; }
function toast(msg) { log.push(["toast", msg]); }
function canProxyTools() { return plan.proxy; }
async function callServerTool(name, args) {
  log.push(["call", name, args]);
  if (plan.fails) throw new Error("refused");
  return { structuredContent: { data: ["p1"] }, _meta: { "pexafy/origin": 1 } };
}
function learnLinks(meta) { log.push(["links", meta]); }
function render(sc) { log.push(["render", sc]); }
let againBusy = false;
""" + _function("async function searchAgain() {") + r"""
searchAgain().then(() => console.log(JSON.stringify(log)));
"""


def _again(**plan):
    plan.setdefault("origin", {"tool": "search_photos",
                               "args": {"english_search_sentence": "a red bicycle"}})
    plan.setdefault("speaks", False)
    plan.setdefault("proxy", True)
    out = subprocess.run([NODE, "-e", _AGAIN], input=json.dumps(plan),
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


@needs_node
def test_a_host_that_takes_a_message_is_asked_as_before():
    log = _again(speaks=True)
    assert log == [["speak", "Run my last Pexafy search again."], ["toast", "Asked again"]]


@needs_node
def test_a_host_that_takes_no_message_gets_the_search_run_from_the_frame():
    log = _again()
    assert ["call", "search_photos", {"english_search_sentence": "a red bicycle"}] in log
    assert ["links", {"pexafy/origin": 1}] in log
    assert log[-1] == ["render", {"data": ["p1"]}]


@needs_node
def test_a_press_that_can_do_nothing_says_so():
    assert _again(proxy=False)[-1] == ["toast", "Ask again in the chat"]
    assert _again(origin=None)[-1] == ["toast", "Ask again in the chat"]
    failed = _again(fails=True)
    assert failed[-1] == ["toast", "refused"]
    assert not any(entry[0] == "render" for entry in failed)


# ── What the cards say, and the deck in a capped frame ───────────────────────

@pytest.mark.parametrize("name, plain, want_name, want_plain", [
    ("Unknown", "Photo by Unknown on Pixabay (https://pixabay.com/x)",
     "", "Photo on Pixabay (https://pixabay.com/x)"),
    ("nympha57 None", "Photo by nympha57 None on Pexels (https://p)",
     "nympha57", "Photo by nympha57 on Pexels (https://p)"),
    ("Ana Lopez", "Photo by Ana Lopez on Unsplash (u)", "Ana Lopez",
     "Photo by Ana Lopez on Unsplash (u)"),
    # Measured: the name field empty, the credit line still saying "Unknown".
    ("", "Photo by Unknown on Pixabay (https://pixabay.com/y)", "",
     "Photo on Pixabay (https://pixabay.com/y)"),
    # A source's user id in place of a name.
    ("", "Photo by 3345557 on Pixabay (z)", "", "Photo on Pixabay (z)"),
    # The API's own placeholder (build_attribution).
    (None, "Photo by Unknown photographer on Pexels (p)", "", "Photo on Pexels (p)"),
])
def test_a_placeholder_is_not_a_photographer(name, plain, want_name, want_plain):
    """Seen in the VS Code grid (2026-10-01): "Photo by Unknown on Pixabay" and a missing
    surname printed as "None"."""
    photo = {"photographer_full_name": name, "attribution": {"plain": plain}}
    if name is None:
        photo["photographer_username"] = "Unknown"
    tooling.prune_photo(photo)
    assert photo["photographer_full_name"] == want_name
    assert photo["attribution"]["plain"] == want_plain


def test_the_name_credited_is_the_one_the_credit_line_prints():
    """Pixabay has no full name: the API credits the username ("Photo by diego_torres on
    Pixabay"), and the grid, which reads the name, wrote "Photo by Unknown"."""
    photo = {"photographer_full_name": "", "photographer_username": "diego_torres",
             "attribution": {"plain": "Photo by diego_torres on Pixabay (x)"}}
    tooling.prune_photo(photo)
    assert photo["photographer_full_name"] == "diego_torres"
    assert "photographer_username" not in photo
    full = {"photographer_full_name": "Ana Lopez", "photographer_username": "ana"}
    tooling.prune_photo(full)
    assert full["photographer_full_name"] == "Ana Lopez"
    absent = {"title": "x"}
    tooling.prune_photo(absent)
    assert "photographer_full_name" not in absent


def test_the_grid_never_prints_a_placeholder_name():
    assert '"Unknown"' not in JS
    credit = _function("function creditText(f) {")
    assert '"Photo" + (f.author ? " by " + f.author : "")' in credit
    for fn in ("function photoLines(f, indent) {", "function selectionRecords() {"):
        assert "creditText(f)" in _function(fn)
    # On screen, the same credit in the reader's language.
    assert "shownCredit(f)" in _function("function creditHtml(f) {")
    assert "Unknown" not in _function("function shownCredit(f) {")


def test_the_deck_fits_a_frame_the_host_caps():
    """Measured in VS Code: the frame is capped (it scrolls inside), and a deck the grid's
    height hid its verbs under the frame's edge."""
    cap = _function("function frameCap() {")
    assert "containerDimensions" in cap
    assert "doc.scrollHeight > inner + 4" in cap
    opener = _function("function openViewer(i, list) {")
    assert opener.index("const cap = frameCap();") < opener.index("deckH = h;")
    assert "if (cap && h > cap) h = cap;" in opener
    # Brought into view once painted, as every deck is (test_widget_html).
    assert opener.index("deckH = h;") < opener.index('reveal(viewerEl, "center");')
