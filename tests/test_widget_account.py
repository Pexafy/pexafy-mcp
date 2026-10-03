"""The grid's account corner, read from `_meta`, and what a reloaded frame keeps of it.

The server puts who is asking, and the wording and button of the allowance panel, on the
result's `_meta` (budget.ACCOUNT_META_KEY). They were in `structuredContent`, the half of
an answer the model reads, and none of it is the model's. The frame reads them in
`learnLinks` and merges the panel's wording with the numbers `sc.budget` still carries
(`budgetOf`). An answer from an earlier server, kept in an older conversation, carries
them in `sc.account` and a whole `sc.budget`, and still paints.

`_meta` does not always reach the frame: a reloaded frame may not be handed it again, and
a host may not relay it — or hands it over empty, or with its own keys alone, which says
no more (`ourMeta`: a `_meta` with no `pexafy/` key counts as absent). So the view keeps
what the anchor's `_meta` said and who is asking (`packAnchor`, `saveUi`), a reloaded frame
takes them back before it draws anything (`takeBack`), and ChatGPT's own copy of the first
result's `_meta` (`window.openai.toolResponseMetadata`) stands in when that result comes
without it.

The functions run in node against the answers a host hands over, over a few stubs for the
DOM they paint; the wiring between them is read statically.
"""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from pexafy_mcp import budget, widget

JS = widget._with_tool_names(widget._WIDGET_JS)
# The reader's language comes first in the frame's script: every text goes through it.
# English only: these run the grid as an English reader sees it, and the whole table
# would put the script past the length of one command-line argument.
I18N_JS = widget._I18N_JS.replace("__I18N__", '{"k": [], "t": {}}')
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")

SIGN_IN = "https://preprod.pexafy.com/auth/login/"
CTA = {"label": "Open in Pexafy", "url": "https://preprod.pexafy.com/?q=red+bicycle"}
PHOTO = {"photo_id": "019e0000-0000-7000-8000-000000000abc",
         "preview_url": "https://thumb.example/x/480w.jpg"}
VISITOR = {"state": "anonymous", "label": "Sign in to Pexafy", "sign_in_label": "Sign in",
           "sign_in_url": SIGN_IN}
MEMBER = {"state": "signed_in", "label": "Signed in", "home_url": "https://preprod.pexafy.com/"}

# The same allowance notice, as this server splits it and as an earlier one sent it.
NUMBERS = {"scope": "day", "limit": 100, "remaining": 2, "used": 98, "state": "warning",
           "signed_in": False,
           "message": "2 searches left today without an account. A free Pexafy account "
                      "lifts this daily limit."}
WORDING = {"short": "2 searches left today", "title": "2 searches left today",
           "detail": "A free Pexafy account lifts this daily limit.",
           "cta_label": "Sign in", "account_url": SIGN_IN}
WALL_NUMBERS = {"scope": "day", "limit": 100, "remaining": 0, "used": 100,
                "state": "exhausted", "signed_in": False,
                "message": "Today's free searches are used up. It resets at midnight UTC. "
                           "A free Pexafy account lifts this daily limit."}
WALL_WORDING = {"short": "Daily limit reached", "title": "Today's free searches are used up",
                "detail": "It resets at midnight UTC. A free Pexafy account lifts this "
                          "daily limit.",
                "cta_label": "Sign in", "account_url": SIGN_IN}


def _between(start: str, end: str) -> str:
    i = JS.index(start)
    return JS[i:JS.index(end, i)]


def test_the_server_splits_a_block_where_the_frame_joins_it():
    """What the frame merges back is exactly what the server took out."""
    whole = dict(NUMBERS, **WORDING)
    assert budget.for_model(whole) == NUMBERS
    assert budget.for_grid(whole) == WORDING
    assert set(NUMBERS) == set(budget.MODEL_FIELDS)
    assert budget.ACCOUNT_META_KEY == "pexafy/account"
    assert f'meta["{budget.ACCOUNT_META_KEY}"]' in _between("function learnLinks(meta) {",
                                                            "let ORIGIN = null;")


def test_the_wiring_a_reloaded_frame_depends_on():
    """Read statically: the order matters, and a node run of each piece cannot see it."""
    render = _between("function render(sc) {", "\n}\n")
    # The site first: fields() writes every photo page on it; then who is asking.
    assert ("takeBack(sc);\n  const built = buildPhotos(sc, null);\n  paintAccount(sc);"
            in render)
    save = _between("function saveUi(now) {", "\n}\n")
    assert "who: acctWho," in save
    # The wall keeps a view too, written at once, with its wording (the wall tests).
    assert "if (wallView) view.wall = wallView;" in save
    assert "saveUi(true);" in _between("function paintWall(sc) {", "\n}\n")
    handler = _between("app.ontoolresult = (params) => {", "\n};\n")
    assert "learnLinks(resultMeta(params));" in handler


# ── Running the frame's own functions ────────────────────────────────────────

_PRELUDE = r"""
const plan = JSON.parse(require("fs").readFileSync(0, "utf8"));
function el(tag) {
  const e = { tag: tag || "div", hidden: false, className: "", textContent: "", title: "",
    attrs: {}, children: [], parent: null, _html: "",
    setAttribute(k, v) { this.attrs[k] = String(v); },
    getAttribute(k) { return this.attrs[k]; },
    appendChild(c) { c.parent = this; this.children.push(c); return c; },
    querySelector(sel) {
      const cls = sel.replace(/^\./, "");
      for (const c of this.children) {
        if ((c.className || "").split(" ").indexOf(cls) !== -1) return c;
        const deep = c.querySelector(sel);
        if (deep) return deep;
      }
      return null;
    },
    addEventListener() {}, contains() { return false; }, focus() {},
    remove() { if (this.parent) this.parent.children = this.parent.children.filter(x => x !== this); },
  };
  Object.defineProperty(e, "innerHTML", {
    get() { return this._html; },
    set(v) { this._html = String(v); if (!v) this.children = []; },
  });
  const set = new Set();
  e.classList = { add: (c) => set.add(c), remove: (c) => set.delete(c), contains: (c) => set.has(c),
    toggle: (c, on) => { if (on === undefined ? !set.has(c) : on) set.add(c); else set.delete(c); } };
  return e;
}
const window = { openai: plan.openai || undefined };
// The host's store: what the frame writes, as the host would keep it.
const written = [];
if (window.openai) {
  window.openai.setWidgetState = (s) => { written.push(JSON.parse(JSON.stringify(s))); };
}
const document = { createElement: (t) => el(t), addEventListener() {} };
const acctEl = el(), acctWrap = el(), acctGlyph = el(), acctDot = el(), acctPop = el(),
      apTitle = el(), apDetail = el(), wallEl = el(), statusEl = el(), headEl = el(),
      gridEl = el(), moreEl = el(), selEl = el(), trailEl = el(), lastEl = el(),
      noteEl = el();
let noteClosed = false, selReordered = false;
const ICON = { user: "u", check: "c", hourglass: "h" };
const GRID_MAX = 16;
let ORIGIN = null, PHOTOS = [], shown = 0, step = 0;
const trail = [];
function normShapes(v) { return Array.isArray(v) ? v : []; }
function shapeList(v) { return normShapes(v).slice(); }
function gridFirst() { return 6; }
function canProxyTools() { return false; }
async function callServerTool() { throw new Error("no proxy here"); }
function speak() { return false; }
function openLink() {}
function toast() {}
function paintGrid() {} function paintTrail() {} function paintShape() {}
function pushContextToModel() {}
// What saveUi reads besides the account: no deck open, nothing liked, the coach idle.
let DECK = [], idx = 0, deckOpen = false;
function coachState() { return {}; }
function keptList() { return []; }
function texts(e) { return e ? e.children.map((c) => c.textContent).filter(Boolean) : []; }
"""


def _chunks() -> str:
    return "\n".join([
        _between("let PEXAFY_HOME", "function hostCan("),
        _between("const LINKS = Object.create(null);", "let ORIGIN = null;"),
        _between("function fields(photo) {", "// Plain text, not a link"),
        _between("function buildPhotos(sc, lead)", "/* How many of one page's photos"),
        _between("/* ── The host's store", "/* ── Surviving a reload of the frame"),
        # packAnchor, and saveUi with what it calls: the view the host's store keeps.
        _between("/* ── Surviving a reload of the frame", "/* What a frame reloaded without its answer's"),
        _between("/* What a frame reloaded without its answer's", "/* Read once, with the first result"),
        _between("function showStep() {", "/* Going back is not undoing"),
        _between("function newPage(ref, built, sc) {", "let shaping = false;"),
        _between("/* ── Who is asking", "/* Which answer a payload is"),
        _between("/* Which answer a payload is", "/* A tool result from the host"),
        _between("/* The `_meta` of a tool result.", "app.ontoolresult = (params) => {"),
    ])


def _run(driver: str, plan: dict) -> dict:
    script = I18N_JS + _PRELUDE + _chunks() + driver
    r = subprocess.run([NODE, "-e", script], input=json.dumps(plan),
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


_CORNER = r"""
const out = [];
for (const s of plan.steps) {
  learnLinks(s.meta);
  paintAccount(s.sc);
  fillPop();
  const go = acctPop.querySelector(".gobtn");
  const row = { shown: !acctWrap.hidden, cls: acctEl.className,
                who: acctWho ? acctWho.state : null, title: apTitle.textContent,
                detail: apDetail.textContent, button: go ? go.textContent : null };
  if (s.wall) {
    row.wall = paintWall(s.sc);
    row.wallTexts = texts(wallEl.querySelector(".wallcard"));
  } else {
    paintLastCall(s.sc);
    row.last = lastEl.hidden ? null : texts(lastEl).concat(texts(lastEl.querySelector(".lc-text")));
  }
  out.push(row);
}
process.stdout.write(JSON.stringify(out));
"""


@needs_node
def test_the_corner_is_painted_from_meta_and_from_an_earlier_server_alike():
    meta = {"pexafy/cta": CTA,
            budget.ACCOUNT_META_KEY: {"account": VISITOR, "budget": WORDING}}
    new, old = _run(_CORNER, {"steps": [
        {"meta": meta, "sc": {"data": [PHOTO], "budget": NUMBERS}},
        # An earlier server: everything in the model's half, no account on `_meta`.
        {"meta": {"pexafy/cta": CTA},
         "sc": {"data": [PHOTO], "budget": dict(NUMBERS, **WORDING), "account": VISITOR}},
    ]})
    assert new == old
    assert new["shown"] and new["who"] == "anonymous"
    assert new["cls"] == "acct warn"
    assert new["title"] == "2 searches left today"
    assert new["detail"] == "A free Pexafy account lifts this daily limit."
    assert new["button"] == "Sign in"
    # Two searches left: the strip over the grid, with its button.
    assert new["last"] == ["Sign in", "2 searches left today",
                           "A free Pexafy account lifts this daily limit."]


@needs_node
def test_the_wall_takes_its_words_and_its_button_from_meta():
    meta = {budget.ACCOUNT_META_KEY: {"account": VISITOR, "budget": WALL_WORDING}}
    new, old = _run(_CORNER, {"steps": [
        {"meta": meta, "sc": {"data": [], "budget": WALL_NUMBERS}, "wall": True},
        {"meta": {}, "sc": {"data": [], "budget": dict(WALL_NUMBERS, **WALL_WORDING),
                            "account": VISITOR}, "wall": True},
    ]})
    assert new == old
    assert new["wall"] is True
    assert new["wallTexts"] == ["Today's free searches are used up",
                                "It resets at midnight UTC. A free Pexafy account lifts "
                                "this daily limit.", "Sign in", "Try again"]


@needs_node
@pytest.mark.parametrize("bare_meta", [None, {}, {"openai/widgetSessionId": "ws_123"}],
                         ids=["none", "empty", "host-keys-only"])
def test_an_answer_without_meta_keeps_who_is_asking_and_says_the_numbers(bare_meta):
    """A result that reached the frame without `_meta` (a host that did not relay it)
    does not change who is asking; the panel's wording belonged to the previous answer,
    so the sentence the model reads stands in for it. A `_meta` that came empty, or with
    the host's keys alone, is the same answer: it says nothing of ours."""
    meta = {budget.ACCOUNT_META_KEY: {"account": VISITOR, "budget": WORDING}}
    _, bare = _run(_CORNER, {"steps": [
        {"meta": meta, "sc": {"data": [PHOTO], "budget": NUMBERS}},
        {"meta": bare_meta, "sc": {"data": [PHOTO], "budget": NUMBERS}},
    ]})
    assert bare["who"] == "anonymous" and bare["shown"]
    assert bare["title"] == "Sign in to Pexafy"
    assert bare["detail"] == NUMBERS["message"]
    assert bare["button"] == "Sign in"


@needs_node
def test_an_answer_that_says_nothing_about_the_account_hides_the_corner():
    """`_meta` came and carries no account (the API sent no plan): nothing to draw,
    as before — not the previous answer's corner."""
    meta = {budget.ACCOUNT_META_KEY: {"account": MEMBER}}
    member, nothing = _run(_CORNER, {"steps": [
        {"meta": meta, "sc": {"data": [PHOTO]}},
        {"meta": {"pexafy/cta": CTA}, "sc": {"data": [PHOTO]}},
    ]})
    assert member["shown"] and member["cls"] == "acct in" and member["who"] == "signed_in"
    assert nothing["shown"] is False and nothing["who"] is None


# ── A reloaded frame ─────────────────────────────────────────────────────────

# render()'s own steps (takeBack, buildPhotos, paintAccount, the anchor page, showStep's
# footer). A first life also packs the anchor the way saveUi does.
_LIFE = r"""
const sc = plan.params.structuredContent;
learnLinks(resultMeta(plan.params));
takeBack(sc);
const built = buildPhotos(sc, null);
paintAccount(sc);
fillPop();
trail.push(newPage(null, built, sc));
showStep();
const later = resultMeta({ structuredContent: sc });   // a second result, no `_meta`
const go = acctPop.querySelector(".gobtn");
process.stdout.write(JSON.stringify({
  footUrl: footUrl, footLabel: footLabel, photoPage: built.photos[0].pexafyUrl,
  who: acctWho ? acctWho.state : null, signIn: acctWho ? acctWho.sign_in_url || null : null,
  panel: apTitle.textContent, detail: apDetail.textContent, button: go ? go.textContent : null,
  laterMeta: later,
  view: { who: acctWho, trail: [packAnchor(trail[0])] },
}));
"""

META = {"pexafy/cta": CTA, budget.ACCOUNT_META_KEY: {"account": VISITOR}}
ANSWER = {"structuredContent": {"data": [PHOTO]}}
NEAR = {"structuredContent": {"data": [PHOTO], "budget": NUMBERS}}
NEAR_META = {"pexafy/cta": CTA,
             budget.ACCOUNT_META_KEY: {"account": VISITOR, "budget": WORDING}}


def _saved_view(answer=ANSWER, meta=META) -> dict:
    """The host's store after a first life that got its `_meta`."""
    first = _run(_LIFE, {"params": dict(answer, _meta=meta)})
    return {"privateContent": {"pexafyView": dict(first["view"], sig=PHOTO["photo_id"] + ":1",
                                                  at=1)}}


@needs_node
def test_the_anchor_s_stub_keeps_what_its_meta_said():
    stub = _saved_view()["privateContent"]["pexafyView"]["trail"][0]
    assert stub == {"shape": [], "cta": CTA, "psig": PHOTO["photo_id"]}
    view = _saved_view(NEAR, NEAR_META)["privateContent"]["pexafyView"]
    assert view["who"] == VISITOR
    assert view["trail"][0] == {"shape": [], "cta": CTA, "psig": PHOTO["photo_id"],
                                "wording": WORDING}


@needs_node
def test_a_frame_reloaded_without_meta_takes_the_link_and_the_account_back():
    out = _run(_LIFE, {"params": ANSWER, "openai": {"widgetState": _saved_view()}})
    assert out["footUrl"] == CTA["url"] and out["footLabel"] == CTA["label"]
    # The site is adopted before the photos are built: their pages are preprod's too.
    assert out["photoPage"] == "https://preprod.pexafy.com/photos/" + PHOTO["photo_id"]
    assert out["who"] == "anonymous" and out["signIn"] == SIGN_IN


@needs_node
def test_chatgpt_s_own_copy_of_the_meta_stands_in_for_the_first_result():
    out = _run(_LIFE, {"params": ANSWER,
                       "openai": {"widgetState": None, "toolResponseMetadata": META}})
    assert out["footUrl"] == CTA["url"]
    assert out["photoPage"] == "https://preprod.pexafy.com/photos/" + PHOTO["photo_id"]
    assert out["who"] == "anonymous"
    # That copy describes the call that opened the frame, and no later one.
    assert out["laterMeta"] is None


def _envelope(where: str, meta: dict) -> dict:
    """ChatGPT's copy as its reference describes it now: an object that "includes
    `status`, `call_tool_result`, and `mcp_tool_result`, preserving the full MCP result
    envelope, including hidden `_meta`", with the `_meta` in one of those places."""
    result = {"content": [], "structuredContent": NEAR["structuredContent"]}
    held = {"status": "success", "call_tool_result": dict(result),
            "mcp_tool_result": dict(result)}
    if where == "top":
        held["_meta"] = meta
    else:
        held[where] = dict(held[where], _meta=meta)
    return held


@needs_node
@pytest.mark.parametrize("where", ["mcp_tool_result", "call_tool_result", "top"])
@pytest.mark.parametrize("meta", [None, {}], ids=["no-meta", "empty-meta"])
def test_chatgpt_s_copy_is_read_inside_the_result_envelope_too(where, meta):
    """Read as if it were the `_meta` itself, the envelope carries no key of ours: no
    link, no account corner, no selection token. The `_meta` inside it is read, and the
    first form still is (test above)."""
    params = dict(NEAR) if meta is None else dict(NEAR, _meta=meta)
    out = _run(_LIFE, {"params": params,
                       "openai": {"widgetState": None,
                                  "toolResponseMetadata": _envelope(where, NEAR_META)}})
    assert out["footUrl"] == CTA["url"]
    assert out["photoPage"] == "https://preprod.pexafy.com/photos/" + PHOTO["photo_id"]
    assert out["who"] == "anonymous"
    assert out["panel"] == WORDING["title"] and out["button"] == "Sign in"
    assert out["laterMeta"] is None


@needs_node
def test_an_envelope_with_nothing_of_ours_gives_the_defaults():
    """An envelope whose `_meta` holds no key of ours, or none at all: nothing to read,
    and not the envelope itself taken for a `_meta`."""
    held = _envelope("mcp_tool_result", {"openai/widgetSessionId": "ws_123"})
    out = _run(_LIFE, {"params": NEAR, "openai": {"widgetState": None,
                                                  "toolResponseMetadata": held}})
    assert out["footUrl"] == "https://pexafy.com"
    assert out["who"] is None


@needs_node
def test_what_the_answer_brings_wins_over_the_view():
    view = _saved_view()
    fresh = {"pexafy/cta": {"label": "Open in Pexafy", "url": "https://pexafy.com/?q=cats"},
             budget.ACCOUNT_META_KEY: {"account": MEMBER}}
    out = _run(_LIFE, {"params": dict(ANSWER, _meta=fresh), "openai": {"widgetState": view}})
    assert out["footUrl"] == "https://pexafy.com/?q=cats"
    assert out["photoPage"] == "https://pexafy.com/photos/" + PHOTO["photo_id"]
    assert out["who"] == "signed_in"


@needs_node
def test_without_meta_or_a_view_the_frame_falls_back_to_the_defaults():
    """The case that cannot be helped — a first result with no `_meta`, from a host with
    no store — draws what it can: the main site, no account corner."""
    out = _run(_LIFE, {"params": ANSWER, "openai": None})
    assert out["footUrl"] == "https://pexafy.com"
    assert out["who"] is None


@needs_node
def test_the_panel_s_wording_comes_back_onto_its_own_answer_only():
    """Near the end of an allowance the corner carries the panel's words and button,
    from `_meta`. Reloaded without it, the frame takes them back — onto the answer they
    came with, recognised by its photographs; over another answer they would describe
    someone else's numbers, and the sentence the model reads stands in."""
    view = _saved_view(NEAR, NEAR_META)
    same = _run(_LIFE, {"params": NEAR, "openai": {"widgetState": view}})
    assert same["who"] == "anonymous"
    assert same["panel"] == WORDING["title"] and same["detail"] == WORDING["detail"]
    assert same["button"] == "Sign in"

    other = {"structuredContent": {"data": [dict(PHOTO, photo_id=PHOTO["photo_id"][:-3] + "def")],
                                   "budget": NUMBERS}}
    moved = _run(_LIFE, {"params": other, "openai": {"widgetState": view}})
    assert moved["who"] == "anonymous"                    # who is asking still holds
    assert moved["footUrl"] == "https://preprod.pexafy.com"   # the site, not that link
    assert moved["panel"] == VISITOR["label"]             # not the other answer's words
    assert moved["detail"] == NUMBERS["message"]
    assert moved["button"] == "Sign in"


@needs_node
def test_an_answer_whose_meta_says_nothing_of_the_account_is_not_given_one():
    """`_meta` came, without an account (the API sent no plan): the view's is not put
    in its place — only an answer that came with no `_meta` at all is filled in."""
    view = _saved_view()
    out = _run(_LIFE, {"params": dict(ANSWER, _meta={"pexafy/cta": CTA}),
                       "openai": {"widgetState": view}})
    assert out["who"] is None
    assert out["footUrl"] == CTA["url"]


NOT_OURS = [{}, {"openai/widgetSessionId": "ws_123"}]


@needs_node
@pytest.mark.parametrize("meta", NOT_OURS, ids=["empty", "host-keys-only"])
def test_a_meta_without_a_key_of_ours_is_no_meta_at_all(meta):
    """A reloaded frame handed `_meta` empty, or with the host's own keys alone: it says
    nothing about the answer, so the frame takes back what its view kept, exactly as when
    `_meta` did not come. It used to count as an answer that named nobody: the corner
    lost its title and its "Sign in" until the next search."""
    view = _saved_view(NEAR, NEAR_META)
    out = _run(_LIFE, {"params": dict(NEAR, _meta=meta), "openai": {"widgetState": view}})
    assert out["footUrl"] == CTA["url"]
    assert out["photoPage"] == "https://preprod.pexafy.com/photos/" + PHOTO["photo_id"]
    assert out["who"] == "anonymous" and out["signIn"] == SIGN_IN
    assert out["panel"] == WORDING["title"] and out["detail"] == WORDING["detail"]
    assert out["button"] == "Sign in"


@needs_node
@pytest.mark.parametrize("meta", NOT_OURS, ids=["empty", "host-keys-only"])
def test_chatgpt_s_copy_stands_in_for_a_meta_without_a_key_of_ours(meta):
    """The same `_meta`, on the first result of a frame with no view, where ChatGPT
    holds its copy: the copy is read. It was ignored, and the footer opened the main
    site's home page from preprod."""
    out = _run(_LIFE, {"params": dict(NEAR, _meta=meta),
                       "openai": {"widgetState": None, "toolResponseMetadata": NEAR_META}})
    assert out["footUrl"] == CTA["url"]
    assert out["photoPage"] == "https://preprod.pexafy.com/photos/" + PHOTO["photo_id"]
    assert out["who"] == "anonymous"
    assert out["panel"] == WORDING["title"] and out["button"] == "Sign in"
    assert out["laterMeta"] is None


# ── The wall, reloaded ───────────────────────────────────────────────────────

# render()'s steps for an answer with no photographs, and what the host's store holds.
_WALL = r"""
const sc = plan.params.structuredContent;
learnLinks(resultMeta(plan.params));
takeBack(sc);
buildPhotos(sc, null);
paintAccount(sc);
const drawn = paintWall(sc);
process.stdout.write(JSON.stringify({
  drawn: drawn, who: acctWho ? acctWho.state : null,
  texts: texts(wallEl.querySelector(".wallcard")),
  stored: written.length ? written[written.length - 1] : null,
}));
"""

WALL_META = {budget.ACCOUNT_META_KEY: {"account": VISITOR, "budget": WALL_WORDING}}
WALL = {"structuredContent": {"success": True, "data": [],
                              "notice": "Today's free searches are used up.",
                              "budget": WALL_NUMBERS}}
WALL_TEXTS = ["Today's free searches are used up",
              "It resets at midnight UTC. A free Pexafy account lifts this daily limit.",
              "Sign in", "Try again"]


def _wall_store() -> dict:
    """The host's store after a first life that drew the wall with its `_meta`."""
    first = _run(_WALL, {"params": dict(WALL, _meta=WALL_META),
                         "openai": {"widgetState": None}})
    assert first["drawn"] is True and first["texts"] == WALL_TEXTS
    return first["stored"]


@needs_node
@pytest.mark.parametrize("meta", [None, {}], ids=["no-meta", "empty-meta"])
def test_a_wall_reloaded_without_meta_is_the_same_wall(meta):
    """The wall wrote no view: reloaded without its `_meta` and without ChatGPT's copy,
    it lost its title ("The allowance is used up") and its "Sign in". It keeps one now —
    who is asking, and its own wording under its signature, since an answer with no
    photographs has no photo ids to be known by — and takes them back."""
    stored = _wall_store()
    view = stored["privateContent"]["pexafyView"]
    assert view["who"] == VISITOR
    assert view["wall"]["wording"] == WALL_WORDING and view["wall"]["sig"]
    # Nothing of it for the model: the store's model half stays empty.
    assert stored["modelContent"] == ""
    params = dict(WALL) if meta is None else dict(WALL, _meta=meta)
    out = _run(_WALL, {"params": params, "openai": {"widgetState": stored}})
    assert out["drawn"] is True and out["who"] == "anonymous"
    assert out["texts"] == WALL_TEXTS


@needs_node
def test_a_wall_s_wording_comes_back_onto_the_same_wall_only():
    """Another wall — another notice — or an answer with photographs is not given this
    wall's words; who is asking comes back either way."""
    stored = _wall_store()
    other = {"structuredContent": dict(WALL["structuredContent"],
                                       notice="This month's searches are used up.")}
    out = _run(_WALL, {"params": other, "openai": {"widgetState": stored}})
    assert out["who"] == "anonymous"
    assert out["texts"] == ["The allowance is used up", WALL_NUMBERS["message"],
                            "Sign in", "Try again"]
    grid = _run(_LIFE, {"params": NEAR, "openai": {"widgetState": stored}})
    assert grid["who"] == "anonymous"
    assert grid["panel"] == VISITOR["label"] and grid["detail"] == NUMBERS["message"]


# ── How long the frame keeps asking ──────────────────────────────────────────

_WATCH = r"""
let now = 0;
const queue = [];
Date.now = () => now;
function setTimeout(fn, ms) { queue.push({ at: now + ms, fn: fn }); return queue.length; }
function clearTimeout() {}
const probes = [];
async function callServerTool(name, args) {
  probes.push([now, name, !!(args && args.check_only)]);
  // Never connected, never back: the watch has to stop on its own.
  return { structuredContent: { connected: false, budget: { state: "exhausted" } } };
}
function canProxyTools() { return true; }
function markAllowanceBack() { throw new Error("not back"); }
async function drain() {
  while (queue.length) {
    queue.sort((a, b) => a.at - b.at);
    const t = queue.shift();
    now = t.at;
    await t.fn();
  }
}
(async () => {
  const out = {};
  watchForConnection();
  await drain();
  out.connect = probes.splice(0);
  now = 0;
  watchForAllowance();
  await drain();
  out.allowance = probes.splice(0);
  out.max = PROBE_FOR_MAX_MS;
  process.stdout.write(JSON.stringify(out));
})();
"""


@needs_node
def test_no_watch_asks_for_longer_than_two_minutes():
    """The grid learns that an account got connected, or that the allowance came back,
    by asking the connect tool's probe (`check_only`) on a timer. It used to ask every
    ten seconds for ten minutes after a wall; a frame nobody is looking at has no
    business calling that long. Both watches run here on a simulated clock, against a
    probe that never says yes."""
    # From the connection watch to the allowance watch, markConnected between them
    # (never reached: the probe never says connected).
    watches = _between("/* Did the connection go through?", "/* The wall lifts: say so")
    r = subprocess.run([NODE, "-e", _WATCH.replace("(async () => {", watches + "\n(async () => {", 1)],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["max"] == 2 * 60 * 1000
    for name in ("connect", "allowance"):
        calls = out[name]
        assert calls, name
        assert all(c[1:] == ["connect_account", True] for c in calls), calls[:2]
        assert calls[-1][0] <= out["max"], (name, calls[-1][0])
    assert [c[0] for c in out["connect"]] == [1000 * i for i in range(1, 31)]
    assert [c[0] for c in out["allowance"]] == [10000 * i for i in range(1, 13)]
