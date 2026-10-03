"""The selection, when the grid cannot post it to the server (widget.selPostFailed).

Measured in ChatGPT, 2026-10-03: a developer connector keeps the content security policy
it was created with, whose `connect-src` did not name this server, and the browser refused
every post to /selection. Two photos liked, "Which photos did I like?": the selection tool
read 0, and the assistant said the selection had expired. The grid swallowed the failure
(`.catch(() => {})`) while its context still pointed the model to the tool.

A failed post now switches the frame's context to the selection itself, as when the server
offers no tool: in the reader's order, renumbered when they unlike, in the dragged order
when they rearrange, and without the image links the tool would not hand over either. The
tool's empty answer leaves room for that list (selection.UNSENT_NOTE).
"""
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import time

import pytest

from pexafy_mcp import selection, widget

JS = widget._with_tool_names(widget._WIDGET_JS)
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _function(signature: str) -> str:
    i = JS.index(signature)
    return JS[i:JS.index("\n}\n", i) + 2]


def _line(start: str) -> str:
    i = JS.index(start)
    return JS[i:JS.index("\n", i) + 1]


# ── The post ─────────────────────────────────────────────────────────────────

# publishSelection and selectionPostFailed as served, with a fetch that does what the plan
# says: "refused" (the page's policy: a rejected promise), "throws", or an HTTP status.
_POST = r"""
const plan = JSON.parse(require("fs").readFileSync(0, "utf8"));
const out = { posts: 0, pushes: 0, warnings: [] };
console.warn = (m) => out.warnings.push(m);
function fetch(url, init) {
  out.posts++;
  if (plan.fetch === "throws") throw new TypeError("blocked");
  if (plan.fetch === "refused") return Promise.reject(new TypeError("Failed to fetch"));
  return Promise.resolve({ ok: plan.fetch < 400, status: plan.fetch });
}
function pushContextToModel() { out.pushes++; }
let selCap = { token: "tok", url: "https://mcp.example/selection", tool: "t" };
const FRAME_ID = "f1";
let ctxRevision = 1;
function selectionRecords() { return [{ rank: 1, photo_id: "p1" }]; }
""" + JS[JS.index("let selSpoken = false;"):JS.index("function writeContextToModel() {")] + r"""
selSpoken = true;
publishSelection();
publishSelection();                       // a second like, met the same way
setTimeout(() => {
  out.failed = selPostFailed;
  console.log(JSON.stringify(out));
}, 50);
"""


def _post(fetch) -> dict:
    out = subprocess.run([NODE, "-e", _POST], input=json.dumps({"fetch": fetch}),
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


@needs_node
@pytest.mark.parametrize("fetch", ["refused", "throws", 401, 413, 500])
def test_a_post_that_fails_hands_the_selection_to_the_context_once(fetch):
    out = _post(fetch)
    assert out["failed"] is True
    # It keeps posting — one that gets through later gives the tool the same list — but
    # the context switches once, and the failure is said, not swallowed.
    assert out["posts"] == 2
    assert out["pushes"] == 1
    assert len(out["warnings"]) == 1
    assert "did not reach the server" in out["warnings"][0]


@needs_node
def test_a_post_that_gets_through_changes_nothing():
    assert _post(200) == {"posts": 2, "pushes": 0, "warnings": [], "failed": False}


def test_no_failure_is_swallowed_any_more():
    publish = _function("function publishSelection() {")
    assert ".catch(() => {})" not in publish and "catch (e) {}" not in publish
    assert publish.count("selectionPostFailed(") == 3


# ── What the model reads then ───────────────────────────────────────────────

# writeContextToModel and the functions it writes the list with, as served. Three photos
# liked in this order: Ben, Ann, Cleo. Then the post fails; then the reader drags Cleo to
# the front (moveKept, as served); then unlikes Ben.
_CONTEXT = r"""
const out = [];
function tagged(url, what) { return url + "?utm=" + what; }
function hostCan(name) { return true; }
function publishSelection() {}
function rememberForModel(text, sel) { out.push({ store: text }); }
function paintSelBlock() {}
function pushContextToModel() {}
function saveUi() {}
const app = { updateModelContext: (m) => out.push(m) };
let selCap = { token: "tok", url: "u", tool: "get_grid_selected_photos" };
let selPostFailed = false;
let selSpoken = false;
let selReordered = false;
let ctxRevision = 0;
const trail = [{}];
let step = 0;
const PHOTOS = [];
let shown = 0;
const keptPhotos = new Map();
""" + _line("function keptList() {") + _function("function creditText(f) {") \
    + _function("function photoLines(f, indent) {") + _function("function selectionText() {") \
    + _function("function moveKept(from, to) {") + _function("function writeContextToModel() {") + r"""
const photo = (id, author, source) => ({ id: id, author: author, source: source,
  pexafyUrl: "https://pexafy.example/photo/" + id, large: "https://thumb.example/" + id + "-1280.jpg" });
for (const f of [photo("pb", "Ben", "Unsplash"), photo("pa", "Ann", "Pexels"), photo("pc", "Cleo", "Pixabay")])
  keptPhotos.set(f.id, f);
const states = {};
function snap(label) {
  writeContextToModel();
  const ctx = out[out.length - 1];
  states[label] = { text: ctx.content[0].text, store: out[out.length - 2].store,
                    tool: ctx.structuredContent.selection_tool,
                    ids: ctx.structuredContent.selected_photo_ids };
}
snap("served");
selPostFailed = true;
snap("failed");
moveKept(2, 0);
snap("dragged");
keptPhotos.delete("pb");
snap("unliked");
console.log(JSON.stringify(states));
"""


@pytest.fixture(scope="module")
def states() -> dict:
    if NODE is None:
        pytest.skip("node is not installed")
    out = subprocess.run([NODE, "-e", _CONTEXT], capture_output=True, text=True,
                         check=True).stdout
    return json.loads(out)


def _order(text: str) -> list[str]:
    ids = [i for i in ("pa", "pb", "pc") if f'photo_id: "{i}"' in text]
    return sorted(ids, key=lambda i: text.index(f'photo_id: "{i}"'))


def test_while_the_server_holds_it_the_context_names_the_tool_and_no_photo(states):
    served = states["served"]
    assert "get_grid_selected_photos returns them with their details." in served["text"]
    assert "photo_id:" not in served["text"]
    assert served["tool"] == "get_grid_selected_photos"
    assert served["ids"] == ["pb", "pa", "pc"]


def test_once_a_post_fails_the_context_carries_the_list_in_the_order_liked(states):
    failed = states["failed"]
    text = failed["text"]
    # The tool is no longer named anywhere the model reads: it would answer "nothing".
    assert "get_grid_selected_photos" not in text and failed["tool"] is None
    assert "Liked by the reader: 3 photos, numbered #1 to #3 on screen in the order liked:" in text
    assert _order(text) == ["pb", "pa", "pc"]
    first = text[text.index("#1\n"):text.index("#2\n")]
    assert "https://pexafy.example/photo/pb?utm=chat" in first
    assert "Attribution: Photo by Ben on Unsplash" in first and "Photographer: Ben" in first
    assert text.index("#2\n") < text.index("Photographer: Ann") < text.index("#3\n")
    # No image link: the tool hands none over where the grid is drawn.
    assert "Image:" not in text and "thumb.example" not in text
    # The host's store carries the same words as the model context.
    assert failed["store"] == text


def test_the_list_follows_a_drag_and_an_unlike(states):
    dragged = states["dragged"]["text"]
    assert "arranged them by dragging, not the order liked:" in dragged
    assert _order(dragged) == ["pc", "pb", "pa"]
    assert dragged.index("#1\n") < dragged.index("Photographer: Cleo") < dragged.index("#2\n")
    unliked = states["unliked"]
    assert "Liked by the reader: 2 photos, numbered #1 to #2" in unliked["text"]
    assert _order(unliked["text"]) == ["pc", "pa"]
    assert unliked["ids"] == ["pc", "pa"]
    assert "#3" not in unliked["text"]


# ── The tool's empty answer ──────────────────────────────────────────────────

def test_an_empty_answer_leaves_room_for_the_list_the_grid_could_not_send(monkeypatch):
    monkeypatch.setattr(selection, "key_for_caller", lambda: "k:unsent")
    selection.clear("k:unsent")
    never = asyncio.run(selection.get_selected_photos())
    assert never["selection_count"] == 0
    assert never["note"].endswith(selection.UNSENT_NOTE)
    # A selection made in a grid since replaced: empty, and no longer "liked nothing",
    # which a grid that could not post here makes false.
    selection.put("k:unsent", [{"photo_id": "p1"}], 1, frame="f1", now=time.time() - 10)
    selection.note_grid("k:unsent")
    replaced = asyncio.run(selection.get_selected_photos())
    assert replaced["selection_count"] == 0 and replaced["note"].endswith(selection.UNSENT_NOTE)
    assert "liked nothing" not in replaced["note"]
    # One that did reach the server is the whole selection, with nothing to reconcile.
    selection.put("k:unsent", [{"photo_id": "p2"}], 1, frame="f2")
    held = asyncio.run(selection.get_selected_photos())
    assert held["selection_count"] == 1 and selection.UNSENT_NOTE not in held["note"]
    selection.clear("k:unsent")
