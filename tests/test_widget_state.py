"""What the grid writes into the host's widget store, and what it reads back.

ChatGPT documents one shape for `setWidgetState`: `modelContent` (shown to the model),
`privateContent` (kept from it) and `imageIds`. The grid's view state — the trail, the
deck, the selection token — used to ride beside them as a fourth top-level key, whose
treatment is undocumented and could hand all of it to the model. It lives inside
`privateContent` now.

The shape is pinned statically, so it holds without node; where node is installed the
store's own code runs against a fake host, which is what shows the composition works:
neither writer drops the other's part, an older state is still read, and a burst is
still one write. The host-context handler runs the same way, for the one thing it does
to the page that a partial notification could undo: the theme.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from pexafy_mcp import widget

JS = widget._with_tool_names(widget._WIDGET_JS)
DOCUMENTED = ["imageIds", "modelContent", "privateContent"]
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


# ── Reading the one call statically ──────────────────────────────────────────

def _strip_comments(src: str) -> str:
    """`src` without // and /* */ comments; string literals are left as they are."""
    out, i, quote = [], 0, None
    while i < len(src):
        c = src[i]
        if quote:
            out.append(c)
            if c == "\\" and i + 1 < len(src):
                out.append(src[i + 1])
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in "\"'`":
            quote = c
            out.append(c)
        elif src.startswith("//", i):
            end = src.find("\n", i)
            i = len(src) if end == -1 else end
            continue
        elif src.startswith("/*", i):
            end = src.find("*/", i + 2)
            i = len(src) if end == -1 else end + 2
            continue
        else:
            out.append(c)
        i += 1
    return "".join(out)


def _split_top_level(body: str) -> list[str]:
    """`body` cut at the commas that are not inside brackets or strings."""
    parts, cur, depth, quote, i = [], [], 0, None, 0
    while i < len(body):
        c = body[i]
        cur.append(c)
        if quote:
            if c == "\\" and i + 1 < len(body):
                cur.append(body[i + 1])
                i += 1
            elif c == quote:
                quote = None
        elif c in "\"'`":
            quote = c
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "," and depth == 0:
            cur.pop()
            parts.append("".join(cur))
            cur = []
        i += 1
    parts.append("".join(cur))
    return parts


def _argument_of(src: str, name: str) -> str:
    """The source of the argument list of the ONE call to `name(` in `src`."""
    assert src.count(name + "(") == 1, f"expected exactly one call to {name}("
    start = src.index(name + "(") + len(name) + 1
    depth, quote, i = 0, None, start
    while i < len(src):
        c = src[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in "\"'`":
            quote = c
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            if depth == 0:
                return src[start:i]
            depth -= 1
        i += 1
    raise AssertionError(f"unbalanced call to {name}(")


def _top_level_keys(obj: str) -> list[str]:
    """The keys of an object literal, in order. A spread, a computed key or anything
    that is not a plain `key: value` fails: each can put an unknown key at the top."""
    body = _strip_comments(obj).strip()
    assert body.startswith("{") and body.endswith("}"), (
        "setWidgetState must be handed an object literal, so its keys can be read here")
    keys = []
    for entry in _split_top_level(body[1:-1]):
        entry = entry.strip()
        if not entry:
            continue                                  # a trailing comma
        assert not entry.startswith("..."), f"a spread can add any key: {entry!r}"
        m = re.match(r"""^(?:"([^"]+)"|'([^']+)'|([A-Za-z_$][\w$]*))\s*(?::|$)""", entry)
        assert m, f"not a plain key: {entry!r}"
        keys.append(m.group(1) or m.group(2) or m.group(3))
    return keys


def test_the_key_reader_would_see_a_fourth_key():
    """The check below is only as good as this reader: it must see an extra key, and
    refuse what it cannot read rather than pass it."""
    literal = "{ modelContent: t, privateContent: { a: 1, b: [2, 3] }, imageIds: [], pexafyView: v }"
    assert _top_level_keys(literal) == ["modelContent", "privateContent", "imageIds", "pexafyView"]
    for bad in ("{ ...rest, modelContent: t }", "{ [k]: v }", "state"):
        with pytest.raises(AssertionError):
            _top_level_keys(bad)


def test_set_widget_state_is_handed_the_three_documented_keys_and_nothing_else():
    """Only `modelContent`, `privateContent` and `imageIds` are documented. Anything else
    at the top level has no documented meaning, and the view state that used to sit
    there carried the whole trail (tens of kilobytes) and the selection token."""
    arg = _argument_of(JS, "setWidgetState")
    assert sorted(_top_level_keys(arg)) == DOCUMENTED
    assert "imageIds: []" in _strip_comments(arg)


# ── Running the store against a fake host ────────────────────────────────────

STORE_START = "/* ── The host's store"
STORE_END = "/* ── Surviving a reload of the frame"

_STORE_HARNESS = r"""
const vm = require("vm");
const plan = JSON.parse(require("fs").readFileSync(0, "utf8"));
const writes = [];
let timers = [];
let nextId = 1;
const openai = { widgetState: plan.boot };
if (!plan.noSetter) {
  openai.setWidgetState = (state) => {
    writes.push({ keys: Object.keys(state), state: JSON.parse(JSON.stringify(state)) });
    openai.widgetState = state;
  };
}
const sandbox = {
  window: { openai: openai },
  setTimeout: (fn, ms) => { const id = nextId++; timers.push({ id: id, fn: fn, ms: ms }); return id; },
  clearTimeout: (id) => { timers = timers.filter((t) => t.id !== id); },
};
vm.createContext(sandbox);
vm.runInContext(plan.block
  + "\n;globalThis.__api = { rememberForModel, rememberView, viewIn, BOOT_STATE, UI_KEY };",
  sandbox);
const api = sandbox.__api;
const steps = [];
for (const op of plan.ops) {
  if (op[0] === "model") api.rememberForModel(op[1], op[2]);
  else if (op[0] === "view") api.rememberView(op[1], !!op[2]);
  else if (op[0] === "flush") { const due = timers; timers = []; due.forEach((t) => t.fn()); }
  else throw new Error("unknown op " + op[0]);
  steps.push({ writes: writes.length, delays: timers.map((t) => t.ms) });
}
process.stdout.write(JSON.stringify({
  writes: writes, steps: steps, uiKey: api.UI_KEY,
  bootView: api.viewIn(api.BOOT_STATE) || null,
}));
"""


def _run_store(ops, boot=None, no_setter=False):
    block = JS[JS.index(STORE_START):JS.index(STORE_END)]
    plan = {"block": block, "ops": ops, "boot": boot, "noSetter": no_setter}
    r = subprocess.run([NODE, "-e", _STORE_HARNESS], input=json.dumps(plan),
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


SEL_1 = {"selection_count": 1, "selection_revision": 1, "selected_photo_ids": ["p1"]}
SEL_2 = {"selection_count": 2, "selection_revision": 2, "selected_photo_ids": ["p1", "p2"]}
VIEW_1 = {"deck": True, "pid": "p1", "trail": [{"shape": []}], "step": 0, "at": 1,
          "cap": {"token": "tok", "url": "https://mcp.example/selection", "tool": "t"}}
VIEW_2 = {"deck": False, "pid": None, "trail": [{"shape": []}], "step": 0, "at": 2}


@needs_node
def test_every_write_has_the_documented_keys_and_no_part_drops_another():
    out = _run_store([
        ["model", "context one", SEL_1],
        ["flush"],
        ["view", VIEW_1, True],
        ["model", "context two", SEL_2],
        ["flush"],
    ])
    key = out["uiKey"]
    writes = out["writes"]
    assert len(writes) == 3
    for w in writes:
        assert sorted(w["keys"]) == DOCUMENTED
        assert w["state"]["imageIds"] == []
    first, second, third = (w["state"] for w in writes)
    assert first["modelContent"] == "context one"
    assert first["privateContent"] == SEL_1                    # no view yet, none invented
    # The view goes in beside the selection, and the text is untouched by it…
    assert second["modelContent"] == "context one"
    assert second["privateContent"] == dict(SEL_1, **{key: VIEW_1})
    # …and the next model write keeps the view, token included.
    assert third["modelContent"] == "context two"
    assert third["privateContent"] == dict(SEL_2, **{key: VIEW_1})


@needs_node
def test_a_burst_is_one_write_and_now_is_written_at_once():
    out = _run_store([
        ["model", "a", SEL_1],
        ["view", VIEW_1],
        ["model", "b", SEL_2],
        ["view", VIEW_2],
        ["flush"],
        ["model", "c", SEL_1],
        ["view", VIEW_1, True],
    ])
    key = out["uiKey"]
    steps = out["steps"]
    assert [s["writes"] for s in steps] == [0, 0, 0, 0, 1, 1, 2]
    assert steps[0]["delays"] == [300]
    assert steps[3]["delays"] == [300]                          # one timer, re-armed
    assert steps[6]["delays"] == []                             # `now` took the pending one with it
    burst, urgent = (w["state"] for w in out["writes"])
    assert burst["modelContent"] == "b"
    assert burst["privateContent"] == dict(SEL_2, **{key: VIEW_2})
    assert urgent["modelContent"] == "c"
    assert urgent["privateContent"] == dict(SEL_1, **{key: VIEW_1})


@needs_node
def test_a_state_written_by_the_previous_version_is_read_and_moved_inside():
    """A conversation open across the deploy holds the view at the top level. It is
    restored from there, and the first write — whichever part it is — carries it into
    `privateContent` with the rest of what the host held."""
    legacy = {"modelContent": "old context", "privateContent": SEL_1, "pexafyView": VIEW_1,
              "imageIds": ["file_stale"]}
    out = _run_store([["model", "new context", SEL_2], ["flush"]], boot=legacy)
    key = out["uiKey"]
    assert key == "pexafyView"
    assert out["bootView"] == VIEW_1
    (write,) = out["writes"]
    assert sorted(write["keys"]) == DOCUMENTED                  # nothing left at the top
    state = write["state"]
    assert state["modelContent"] == "new context"
    assert state["privateContent"] == dict(SEL_2, **{key: VIEW_1})
    assert state["imageIds"] == []

    out = _run_store([["view", VIEW_2, True]], boot=legacy)
    (write,) = out["writes"]
    assert sorted(write["keys"]) == DOCUMENTED
    state = write["state"]
    assert state["modelContent"] == "old context"               # carried until rewritten
    assert state["privateContent"] == dict(SEL_1, **{key: VIEW_2})


@needs_node
def test_a_state_in_the_documented_shape_is_read_from_private_content():
    key = "pexafyView"
    current = {"modelContent": "context", "privateContent": dict(SEL_1, **{key: VIEW_1}),
               "imageIds": []}
    out = _run_store([["model", "context two", SEL_2], ["flush"]], boot=current)
    assert out["bootView"] == VIEW_1
    (state,) = (w["state"] for w in out["writes"])
    assert state["privateContent"] == dict(SEL_2, **{key: VIEW_1})
    # Both places filled: the documented one wins.
    both = {"privateContent": {key: VIEW_2}, key: VIEW_1}
    assert _run_store([], boot=both)["bootView"] == VIEW_2
    # Nothing there, or nothing readable: no view, and no failure.
    assert _run_store([], boot=None)["bootView"] is None
    assert _run_store([], boot={"privateContent": "junk", key: 3})["bootView"] is None


@needs_node
def test_a_host_without_the_store_is_left_alone():
    """Claude and every MCP Apps host: no `window.openai.setWidgetState`, no write, no
    error — the view simply does not survive a reload there, as before."""
    out = _run_store([["model", "a", SEL_1], ["view", VIEW_1, True], ["flush"]],
                     boot={"pexafyView": VIEW_1}, no_setter=True)
    assert out["writes"] == []
    assert all(s["delays"] == [] for s in out["steps"])


# ── The theme ────────────────────────────────────────────────────────────────

_THEME_HARNESS = r"""
const vm = require("vm");
const plan = JSON.parse(require("fs").readFileSync(0, "utf8"));
const applied = [];
const synced = [];
const sandbox = {
  app: { getHostContext: () => plan.hostContext },
  applyDocumentTheme: (t) => { applied.push(t === undefined ? "<undefined>" : t); },
  syncDisplayMode: (c) => { synced.push(c === undefined ? null : c); },
};
vm.createContext(sandbox);
vm.runInContext(plan.handler, sandbox);
for (const c of plan.notifications) sandbox.app.onhostcontextchanged(c);
vm.runInContext(plan.connect, sandbox);
process.stdout.write(JSON.stringify({ applied: applied, synced: synced.length }));
"""


def _run_theme(notifications, host_context):
    start = JS.index("app.onhostcontextchanged = (ctx) => {")
    handler = JS[start:JS.index("\n};\n", start) + 3]
    connect = next(ln for ln in JS.splitlines()
                   if "getHostContext()" in ln and "applyDocumentTheme(" in ln)
    plan = {"handler": handler, "connect": connect, "notifications": notifications,
            "hostContext": host_context}
    r = subprocess.run([NODE, "-e", _THEME_HARNESS], input=json.dumps(plan),
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@needs_node
def test_a_partial_host_context_does_not_reset_the_theme():
    """The SDK writes the theme into `data-theme` as given; a notification without one
    used to write "undefined" and drop the host's dark theme."""
    out = _run_theme([{"displayMode": "fullscreen"}, {"theme": "dark"}, None,
                      {"safeAreaInsets": {"top": 20}}], host_context={"displayMode": "inline"})
    assert out["applied"] == ["dark"]
    assert out["synced"] == 4                     # the display mode still follows every one
    assert _run_theme([], host_context={"theme": "light"})["applied"] == ["light"]
    assert _run_theme([], host_context=None)["applied"] == []


@needs_node
def test_the_widget_script_parses(tmp_path):
    """Four thousand lines of JavaScript inside a Python string: nothing else here would
    notice a syntax error before a host rendered a blank frame."""
    path = tmp_path / "widget.mjs"
    path.write_text(JS, encoding="utf-8")
    r = subprocess.run([NODE, "--check", str(path)], capture_output=True, text=True,
                       timeout=60)
    assert r.returncode == 0, r.stderr
