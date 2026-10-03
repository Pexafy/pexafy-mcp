"""The grid's view, kept by the server for a host that keeps none (selection.put_view).

VS Code gives the frame no store and mounts the grid again from the answer at every
turn of the conversation. Measured on preprod, 2026-10-01: two photos liked, "Which
photos did I like?" sent — the fresh frame posted an empty selection over the reader's
one second before the model read it, and the selection tool answered "nothing liked";
the shape filter and the deck were gone from the grid as well.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from unittest.mock import patch

import pytest

from pexafy_mcp import selection, widget

JS = widget._with_tool_names(widget._WIDGET_JS)
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _function(signature: str) -> str:
    i = JS.index(signature)
    return JS[i:JS.index("\n}\n", i) + 2]


@pytest.fixture(autouse=True)
def _clean_views():
    selection._VIEWS.clear()
    yield
    selection._VIEWS.clear()


# ── The store ────────────────────────────────────────────────────────────────

VIEW = {"kept": ["p1", "p2"], "deck": False, "trail": [{"shape": ["portrait"]}],
        "step": 0, "sig": "p1:16", "at": 1}


def test_a_view_belongs_to_one_person_and_one_answer():
    assert selection.put_view("a:me", "p1:16", VIEW, now=100.0)
    assert selection.get_view("a:me", "p1:16", now=101.0) == VIEW
    assert selection.get_view("a:me", "p9:16", now=101.0) is None       # another answer
    assert selection.get_view("a:you", "p1:16", now=101.0) is None      # another person
    later = 100.0 + selection.VIEW_TTL + 1
    assert selection.get_view("a:me", "p1:16", now=later) is None       # gone with the selection


def test_what_is_not_a_view_is_not_kept():
    assert not selection.put_view("a:me", "p1:16", ["not", "an", "object"])
    assert not selection.put_view("a:me", "", VIEW)
    assert not selection.put_view("a:me", "s" * (selection.VIEW_SIG_MAX + 1), VIEW)
    assert not selection.put_view("", "p1:16", VIEW)
    huge = dict(VIEW, trail=[{"photos": ["x" * 1000] * 100}])
    assert not selection.put_view("a:me", "p1:16", huge)
    assert selection._VIEWS == {}


def test_the_number_of_views_is_bounded_oldest_first():
    with patch.object(selection, "VIEW_MAX_KEYS", 3):
        for i in range(5):
            selection.put_view("a:me", f"p{i}:16", VIEW, now=100.0 + i)
    assert sorted(selection._VIEWS) == ["a:me|p2:16", "a:me|p3:16", "a:me|p4:16"]


def test_the_answer_says_where_the_view_goes():
    with patch.object(selection, "PUBLIC_URL", "https://mcp.example"), \
         patch.object(selection, "key_for_caller", lambda: "a:me"):
        meta = selection.meta_for_result()
    assert meta["view_url"] == "https://mcp.example/view"
    assert selection.read_token(meta["token"]) == "a:me"


# ── The route ────────────────────────────────────────────────────────────────

def test_a_view_is_kept_under_the_token_of_its_answer():
    """An anonymous person is their address: two people behind one address who ran the
    same search share a person key, and read each other's grid when the view was kept
    by person (seen on preprod, 2026-10-01, a test client and VS Code on one machine).
    Every answer carries a token of its own, even two in the same second."""
    now = time.time()
    first = selection.issue_token("a:me", now=now)
    second = selection.issue_token("a:me", now=now)
    assert first != second
    assert selection.read_token(first) == selection.read_token(second) == "a:me"
    assert selection.view_key(first) != selection.view_key(second)
    assert selection.view_key(first).startswith("a:me|")
    assert selection.view_key("forged") == ""


def test_the_route_keeps_a_view_and_hands_it_back_to_its_answer_only():
    from starlette.testclient import TestClient

    from pexafy_mcp import server

    mine = selection.issue_token("a:me")
    my_other_answer = selection.issue_token("a:me")
    theirs = selection.issue_token("a:you")
    post = {"headers": {"content-type": "text/plain"}}
    with TestClient(server.build_server().http_app()) as client:
        pre = client.options("/view")
        assert pre.status_code == 200
        assert pre.headers["access-control-allow-origin"] == "*"
        assert client.post("/view", content=json.dumps({"token": "forged", "sig": "p1:16"}),
                           **post).status_code == 401
        assert client.post("/view", content=json.dumps({"token": mine}), **post).status_code == 400
        assert client.post("/view", content=json.dumps({"token": mine, "sig": "p1:16",
                                                        "view": "text"}), **post).status_code == 400
        assert client.post("/view", content="x" * (selection.VIEW_MAX + 8192),
                           **post).status_code == 413
        # Nothing kept yet: a frame's first read finds nothing, and says so plainly.
        first = client.post("/view", content=json.dumps({"token": mine, "sig": "p1:16"}), **post)
        assert first.status_code == 200 and first.json() == {"view": None}
        kept = client.post("/view", content=json.dumps({"token": mine, "sig": "p1:16",
                                                        "view": VIEW}), **post)
        assert kept.status_code == 200 and kept.json() == {"ok": True}
        back = client.post("/view", content=json.dumps({"token": mine, "sig": "p1:16"}), **post)
        assert back.json() == {"view": VIEW}
        other = client.post("/view", content=json.dumps({"token": theirs, "sig": "p1:16"}), **post)
        assert other.json() == {"view": None}
        # The same address, another answer with the same photographs: not this grid.
        twin = client.post("/view", content=json.dumps({"token": my_other_answer,
                                                        "sig": "p1:16"}), **post)
        assert twin.json() == {"view": None}


# ── The frame ────────────────────────────────────────────────────────────────

_FRAME = r"""
const plan = JSON.parse(require("fs").readFileSync(0, "utf8"));
const log = [];
const window = { openai: plan.hostStore ? { setWidgetState: () => {} } : undefined };
let selCap = { token: "tok", url: "https://mcp.example/selection", tool: "t",
               view: "https://mcp.example/view" };
const BOOT_STATE = plan.boot || null;
function viewIn(st) { return st && st.pexafyView ? st.pexafyView : null; }
function resultSig() { return "p1:16"; }
let ctxHold = 0;
function pushContextToModel() { log.push(["push", ctxHold]); }
function saveUi() { log.push(["save", viewPending]); }
function applyView(ui, fromServer) { log.push(["apply", ui, fromServer, ctxHold]); }
function fetch(url, init) {
  log.push(["fetch", url, JSON.parse(init.body)]);
  if (plan.slow) return new Promise(() => {});
  return Promise.resolve({ ok: true, json: () => Promise.resolve({ view: plan.served }) });
}
let uiRestored = false;
""" + JS[JS.index("const VIEW_BUDGET = "):JS.index("function hostStores() {")] \
    + _function("function hostStores() {") + _function("function serverViewUrl() {") \
    + _function("function serverViewGet() {") + _function("function restoreUi() {") + r"""
restoreUi();
log.push(["pending", viewPending, ctxHold]);
if (plan.acts) userActed = true;
setTimeout(() => {
  log.push(["end", viewPending, ctxHold]);
  console.log(JSON.stringify(log));
  process.exit(0);                       // not waiting out the read's own timer
}, plan.slow ? READ_WAIT_MS + 200 : 50);
"""


def _frame(**plan):
    out = subprocess.run([NODE, "-e", _FRAME], input=json.dumps(plan),
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


SERVED = {"kept": ["p1", "p2"], "sig": "p1:16", "at": 1}


@needs_node
def test_a_frame_without_a_host_store_reads_the_server_before_it_writes():
    log = _frame(served=SERVED)
    assert log[0] == ["fetch", "https://mcp.example/view", {"token": "tok", "sig": "p1:16"}]
    # Held while the read is in flight: no model context, no selection, no view written.
    assert log[1] == ["pending", True, 1]
    # Then the server's copy is put back — not held to the host store's fifteen
    # minutes — and only then is anything said, once.
    assert log[2] == ["apply", SERVED, True, 1]
    assert log[3:] == [["push", 0], ["save", False], ["end", False, 0]]


@needs_node
def test_a_reader_who_acts_meanwhile_keeps_what_they_did():
    log = _frame(served=SERVED, acts=True)
    assert not any(entry[0] == "apply" for entry in log)
    assert log[-3:] == [["push", 0], ["save", False], ["end", False, 0]]


@needs_node
def test_a_server_that_does_not_answer_holds_the_grid_a_moment_only():
    log = _frame(served=SERVED, slow=True)
    assert not any(entry[0] == "apply" for entry in log)
    assert log[-3:] == [["push", 0], ["save", False], ["end", False, 0]]


@needs_node
def test_a_host_with_a_store_is_restored_from_it_and_the_server_is_not_asked():
    boot = {"pexafyView": SERVED}
    log = _frame(served=None, hostStore=True, boot=boot)
    assert log[0] == ["apply", SERVED, False, 0]
    assert not any(entry[0] == "fetch" for entry in log)
    # Nothing held: ChatGPT is exactly as before.
    assert log[1] == ["pending", False, 0]


_PUBLISH = r"""
const posts = [];
function fetch(url, init) { posts.push([url, JSON.parse(init.body).items.length]);
                            return Promise.resolve(); }
let selCap = { token: "tok", url: "https://mcp.example/selection", tool: "t" };
const FRAME_ID = "f1";
let ctxRevision = 1;
function selectionRecords() { return []; }
""" + JS[JS.index("let selSpoken = false;"):JS.index("function writeContextToModel() {")] + r"""
publishSelection();                  // a fresh frame: nothing to say
selSpoken = true;                    // a like, or an unlike
publishSelection();
console.log(JSON.stringify(posts));
"""


@needs_node
def test_a_fresh_frame_says_nothing_to_the_server():
    out = subprocess.run([NODE, "-e", _PUBLISH], capture_output=True, text=True,
                         check=True).stdout
    assert json.loads(out) == [["https://mcp.example/selection", 0]]
    keep = _function("function setKeptPhoto(f, on) {")
    assert keep.index("selSpoken = true;") < keep.index("pushContextToModel();")


_PUT = r"""
const plan = JSON.parse(require("fs").readFileSync(0, "utf8"));
const posts = [];
const window = {};
let selCap = { token: "tok", url: "u", tool: "t", view: "https://mcp.example/view" };
function trimTrail(budget) { return [{ shape: [], budget: budget }]; }
function fetch(url, init) { posts.push([url, init.body.length, JSON.parse(init.body), init.keepalive]);
                            return Promise.resolve(); }
""" + JS[JS.index("const VIEW_BUDGET = "):JS.index("function hostStores() {")] \
    + _function("function hostStores() {") + _function("function serverViewUrl() {") \
    + _function("function viewBody(view) {") + _function("function serverViewPut(view, now) {") + r"""
viewPending = plan.pending || false;
viewToken = "tok";
serverViewPut(plan.view, false);
serverViewPut(plan.view, false);           // a burst: one post
setTimeout(() => {
  serverViewPut(plan.view, true);          // `now`: at once
  console.log(JSON.stringify({ posts: posts, budget: VIEW_BUDGET }));
}, 400);
"""


def _put(**plan):
    out = subprocess.run([NODE, "-e", _PUT], input=json.dumps(plan), capture_output=True,
                         text=True, check=True).stdout
    return json.loads(out)


@needs_node
def test_the_view_goes_to_the_server_once_per_burst_and_fits():
    small = {"sig": "p1:16", "kept": ["p1"], "trail": [{"shape": []}], "step": 0, "at": 1}
    out = _put(view=small)
    assert len(out["posts"]) == 2
    url, size, body, keepalive = out["posts"][0]
    assert url == "https://mcp.example/view"
    assert body == {"token": "tok", "sig": "p1:16", "view": small}
    assert keepalive is True
    # A trail too large for the server: the oldest pages go first (trimTrail), the likes
    # and the deck stay.
    big = dict(small, trail=[{"photos": ["x" * 2000] * 60}], step=1)
    out = _put(view=big)
    url, size, body, keepalive = out["posts"][0]
    assert size <= out["budget"]
    assert body["view"]["kept"] == ["p1"]
    assert body["view"]["trail"] == [{"shape": [], "budget": out["budget"] / 2}]
    # Nothing at all while the frame is still reading what to put back.
    assert _put(view=small, pending=True)["posts"] == []
