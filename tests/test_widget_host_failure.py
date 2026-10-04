"""The grid when the host fails to run a call from the frame (2026-10-05).

ChatGPT runs a call from the frame (≈, the shape filter, connect) with a piece of its
own page that it loads on first use, `callMcpWithAuth`, published 2026-10-04. In one
reader's browser that load failed on every press, and a private window worked. The
grid printed the host's words as its toast: "MCP error -32000: Failed to fetch
dynamically imported module: https://chatgpt.com/cdn/assets/98d76dd9-….js".

  * ≈: the same request goes to the conversation (askSimilar's turn, with the photo_id
    and the words of the search), where the host runs the call on its servers.
  * From the first such failure the frame makes no call of its own: the next ≈ goes
    to the conversation at once, and the shape filter is hidden.
  * Connect: the model is asked, since no sign-in opened.
  * No toast repeats the host's words any more; they go to the console.
"""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from pexafy_mcp import tooling, widget

JS = widget._with_tool_names(widget._WIDGET_JS)
I18N_JS = widget._I18N_JS.replace("__I18N__", '{"k": [], "t": {}}')
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")

# The toast of 2026-10-05, word for word, and the other browsers' wording of it.
CHATGPT = ("MCP error -32000: Failed to fetch dynamically imported module: "
           "https://chatgpt.com/cdn/assets/98d76dd9-hwo09qn438w920o2.js")
FIREFOX = ("MCP error -32000: error loading dynamically imported module: "
           "https://chatgpt.com/cdn/assets/98d76dd9-hwo09qn438w920o2.js")
SAFARI = "MCP error -32000: Importing a module script failed."
BY_IMAGE = tooling.PUBLIC_TOOL_NAMES["search_photos_by_image"]
CONNECT = tooling.PUBLIC_TOOL_NAMES["connect_account"]


def _function(signature: str) -> str:
    i = JS.index(signature)
    return JS[i:JS.index("\n}\n", i) + 2]


def _host_block() -> str:
    """HOST_LOAD_FAILED, hostLoadFailed() and canProxyTools(), as the grid has them."""
    i = JS.index("const HOST_LOAD_FAILED =")
    return JS[i:JS.index("\n}\n", JS.index("function canProxyTools() {")) + 2]


_STUBS = I18N_JS + r"""
const plan = JSON.parse(require("fs").readFileSync(0, "utf8"));
const log = [];
console.warn = (m) => log.push(["warn", m]);
const window = plan.openai ? { openai: { callTool() {} } } : {};
function hostCan(name) { return name === "serverTools" && !!plan.serverTools; }
const trail = [{ ref: null, origin: { tool: "search_photos",
                                      args: { english_search_sentence: "a red bicycle" } } }];
let step = 0, refining = false, acctBudget = null, acctMeta = null, wallSeenAt = 0;
const WALL_GUARD_MS = 1000;
const gridEl = { classList: { add() {}, remove() {} } }, headEl = gridEl;
function refineExhausted() { return false; }
function toast(msg) { log.push(["toast", msg]); }
function speak(text) { log.push(["speak", text]); return !!plan.speaks; }
function followTheAnswer() { log.push(["follow"]); }
async function callServerTool(name, args) {
  log.push(["call", name, args]);
  if (plan.error) throw new Error(plan.error);
  return { structuredContent: { data: [{}, {}] } };
}
function learnLinks() {}
function paintAccount() {}
function budgetOf() { return null; }
function buildPhotos(sc, lead) { return { photos: [lead, { id: "p2" }] }; }
function newPage(f) { return { ref: f }; }
function showStep() { log.push(["painted"]); }
function enterPage() {}
function beatTheAnchor() {}
function fillPop() {}
function openPop() {}
function watchForConnection() { log.push(["watch"]); }
function openLink(url) { log.push(["open", url]); }
const CONNECT_ASK = "Connect my Pexafy account.";
"""


def _run(body: str, **plan) -> dict:
    plan.setdefault("serverTools", True)
    plan.setdefault("speaks", True)
    script = (_STUBS + _host_block() + _function("function wordsOf(page) {")
              + _function("function speakSimilar(f, words) {")
              + _function("async function refineFrom(f) {")
              + _function("async function connectAccount(url) {") + body)
    out = subprocess.run([NODE, "-e", script], input=json.dumps(plan),
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


_SIMILAR = r"""
(async () => {
  const f = { id: "019e1ecb-0039-7da6-b1ca-987ee4d337c1", description: "red bicycle" };
  const ok = await refineFrom(f);
  const proxy = canProxyTools();
  process.stdout.write(JSON.stringify({ ok, proxy, log }));
})();
"""


@needs_node
def test_a_host_that_cannot_load_its_call_sends_the_request_to_the_conversation():
    run = _run(_SIMILAR, error=CHATGPT)
    log = run["log"]
    assert log[0] == ["call", BY_IMAGE, {"photo_id": "019e1ecb-0039-7da6-b1ca-987ee4d337c1",
                                         "english_search_sentence": "a red bicycle"}]
    # The turn carries the photo_id and the words of the search.
    assert ["speak", 'Find me more photos like this one (red bicycle) — photo_id '
            '"019e1ecb-0039-7da6-b1ca-987ee4d337c1", from my search "a red bicycle".'] in log
    assert ["toast", "Asked for more like this one"] in log and ["follow"] in log
    # True, as askSimilar is: the deck closes the same way (superLike).
    assert run["ok"] is True
    # The host's words are in the console, never in a toast.
    assert not any(e[0] == "toast" and "MCP error" in e[1] for e in log)
    assert any(e[0] == "warn" and CHATGPT in e[1] for e in log)
    # And this frame calls no more: the next ≈ goes to the conversation at once.
    assert run["proxy"] is False


@needs_node
@pytest.mark.parametrize("message", [FIREFOX, SAFARI, "ChunkLoadError: Loading chunk 812 failed."])
def test_every_browsers_wording_of_the_failure_is_recognised(message):
    assert _run(_SIMILAR, error=message)["proxy"] is False


@needs_node
def test_any_other_failure_still_reaches_the_conversation_but_the_frame_keeps_calling():
    run = _run(_SIMILAR, error="MCP error -32603: upstream timeout")
    assert run["ok"] is True
    assert any(e[0] == "speak" for e in run["log"])
    assert not any(e[0] == "toast" and "MCP error" in e[1] for e in run["log"])
    assert run["proxy"] is True


@needs_node
def test_a_host_that_takes_no_message_says_it_in_its_own_words():
    run = _run(_SIMILAR, error=CHATGPT, speaks=False)
    assert run["ok"] is False
    assert run["log"][-1] == ["toast", "Could not fetch similar photos"]


@needs_node
def test_a_call_that_works_is_unchanged():
    run = _run(_SIMILAR)
    assert run["ok"] is True and run["proxy"] is True
    assert ["painted"] in run["log"]
    assert not any(e[0] == "speak" for e in run["log"])


_CONNECT = r"""
connectAccount("https://pexafy.com/auth/login/").then(() => {
  process.stdout.write(JSON.stringify({ log, proxy: canProxyTools() }));
});
"""


@needs_node
def test_connect_asks_the_model_when_the_call_never_left_the_page():
    run = _run(_CONNECT, error=CHATGPT, openai=True)
    assert run["log"][0][:2] == ["call", CONNECT]
    assert ["speak", "Connect my Pexafy account."] in run["log"]
    assert ["watch"] not in run["log"]
    assert run["proxy"] is False


@needs_node
def test_connect_still_reads_a_refusal_as_the_challenge():
    run = _run(_CONNECT, error="MCP error -32001: Unauthorized", openai=True)
    assert run["log"][-1] == ["watch"]
    assert not any(e[0] == "speak" for e in run["log"])


def test_no_toast_prints_the_hosts_own_words():
    for signature in ("async function refineFrom(f) {", "async function applyShapes(list) {"):
        assert "toast((e && e.message) ? e.message" not in _function(signature), signature
    shape = JS[JS.index("let shaping = false;"):JS.index("/* ── The deck ")]
    assert 'toast(tx("Could not filter by shape"));' in shape
    assert "toast((e && e.message) ? e.message : tx(\"Could not filter by shape\"))" not in shape


def test_the_probes_learn_it_too():
    for signature in ("function watchForConnection() {", "function watchForAllowance() {"):
        assert "hostLoadFailed(e);" in _function(signature), signature
