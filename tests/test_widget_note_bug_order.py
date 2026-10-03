"""Three things asked for on 2026-10-01, run in node over stubs of the page.

- Under the name, on every grid, a line in the site's violet: the photos the reader
  likes go to the assistant, by its name where the host is known. Closable.
- Next to the account, a bug: the contact form, opened on "Bug reports", in the
  reader's language.
- The liked photos, reordered by their handle (six dots and the rank) or the arrow keys:
  the rank is the order the assistant is given.
"""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from pexafy_mcp import widget

JS = widget._with_tool_names(widget._WIDGET_JS)
I18N_JS = widget._with_tool_names(widget._I18N_JS)
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _function(signature: str) -> str:
    i = JS.index(signature)
    return JS[i:JS.index("\n}\n", i) + 2]


def _node(script: str):
    out = subprocess.run([NODE], input=script, capture_output=True, text=True, check=True).stdout
    return json.loads(out)


_NOTE = r"""
const window = { openai: plan.openai ? {} : undefined };
const navigator = { language: plan.lang || "en" };
const document = { documentElement: {} };
""" + I18N_JS + r"""
readLang();
const app = { getHostVersion: () => (plan.host ? { name: plan.host } : null) };
function slug(v) { return String(v || "").toLowerCase().replace(/[^a-z0-9]+/g, "-"); }
function surface() { return "mcp-host"; }
const noteEl = { hidden: true }, noteText = { textContent: "" };
const noteX = { title: "", setAttribute() {} };
const wallEl = { hidden: !plan.wall };
let PHOTOS = plan.photos ? [1, 2] : [];
let hostMeta = plan.meta || null;
let noteClosed = !!plan.closed;
""" + JS[JS.index("const ASSISTANTS = ["):JS.index("function assistantName() {")] \
    + _function("function assistantName() {") + _function("function paintNote() {") + r"""
paintNote();
process.stdout.write(JSON.stringify({ hidden: noteEl.hidden, text: noteText.textContent }));
"""


def _note(**plan):
    plan.setdefault("photos", True)
    return _node("const plan = " + json.dumps(plan) + ";\n" + _NOTE)


@needs_node
def test_the_line_names_the_assistant_the_server_or_the_host_names():
    assert _note(meta={"assistant": "Cursor"}) == {
        "hidden": False, "text": "Photos you like are shared with Cursor"}
    assert _note(openai=True)["text"] == "Photos you like are shared with ChatGPT"
    assert _note(host="Visual Studio Code")["text"] == "Photos you like are shared with GitHub Copilot"
    assert _note(host="Claude")["text"] == "Photos you like are shared with Claude"
    # A host nobody knows: the assistant, unnamed.
    assert _note(host="Some Inspector")["text"] == "Photos you like are shared with the assistant"


@needs_node
def test_the_line_speaks_the_reader_s_language():
    assert _note(lang="fr", meta={"assistant": "ChatGPT"})["text"] == \
        "Les photos que vous aimez sont transmises à ChatGPT"
    assert _note(lang="de")["text"] == \
        "Fotos, die dir gefallen, werden an den Assistenten weitergegeben"


_SEL_TITLE = _NOTE[:_NOTE.index("paintNote();\nprocess.stdout")] \
    + _function("function selTitle(n) {") + r"""
process.stdout.write(JSON.stringify(selTitle(plan.n)));
"""


def _sel_title(**plan):
    return _node("const plan = " + json.dumps(plan) + ";\n" + _SEL_TITLE)


@needs_node
def test_the_liked_strip_names_the_assistant_as_the_line_does():
    """Claude.ai, 2026-10-02: the line said Claude, the strip's title under it "the
    assistant"."""
    assert _sel_title(n=1, host="Claude") == "Liked photo — use it directly in the chat with Claude"
    assert _sel_title(n=3, meta={"assistant": "ChatGPT"}) == \
        "Liked photos — use them directly in the chat with ChatGPT"
    assert _sel_title(n=2, host="Some Inspector") == \
        "Liked photos — use them directly in the chat with the assistant"
    assert _sel_title(n=1, lang="fr", host="Claude") == \
        "Photo aimée — utilisez-la directement dans la conversation avec Claude"
    assert _sel_title(n=5, lang="ru", host="Claude") == \
        "Отмеченные фото — используйте их прямо в чате с Claude"
    assert _sel_title(n=2, lang="ja", host="Claude") == "いいねした写真 — Claude とのチャットでそのまま使えます"


_COACH = _NOTE[:_NOTE.index("paintNote();\nprocess.stdout")] + r"""
const ICON = { starS: "≈", heartTip: "♥" };
function esc(s) { return String(s); }
""" + JS[JS.index("const COACH_NAMED = new Map(["):JS.index("function coachHtml(src) {")] \
    + _function("function coachHtml(src) {") + r"""
process.stdout.write(JSON.stringify(coachHtml(plan.src)));
"""


def _coach(**plan):
    return _node("const plan = " + json.dumps(plan) + ";\n" + _COACH)


@needs_node
def test_the_coach_names_the_assistant_as_the_line_does():
    """Its four lines said "the assistant" under a line saying Claude (2026-10-02)."""
    like = "Tap {heart} to like a photo — the assistant can then use it"
    done = "Liked! Now ask the assistant to use it in your project"
    assert _coach(src=like, host="Claude") == "Tap <b>♥</b> to like a photo — Claude can then use it"
    assert _coach(src=done, meta={"assistant": "ChatGPT"}) == "Liked! Now ask ChatGPT to use it in your project"
    assert _coach(src=done, lang="fr", host="Claude") == \
        "Aimée ! Demandez maintenant à Claude de l'utiliser dans votre projet"
    # A host nobody knows keeps the assistant unnamed; a line without it is unchanged.
    assert _coach(src=done, host="Some Inspector") == done
    assert _coach(src="Tap a photo to open it", host="Claude") == "Tap a photo to open it"


@needs_node
def test_the_line_goes_with_its_cross_and_where_there_is_nothing_to_like():
    assert _note(closed=True)["hidden"] is True
    assert _note(wall=True)["hidden"] is True
    assert _note(photos=False)["hidden"] is True


def test_the_line_sits_under_the_name_in_the_site_s_violet_and_bold():
    html = widget.GRID_HTML
    head = html[html.index('<div class="gridhead"'):html.index('<div class="grid" id="grid"')]
    assert head.index('class="brandmark"') < head.index('id="sharenote"')
    assert 'id="sharenoteX"' in head
    css = widget._STYLE[widget._STYLE.index(".sharenote {"):]
    css = css[:css.index("}")]
    assert "color: var(--primary)" in css and "font-weight: 700" in css
    assert "justify-self: center" in css and "grid-column: 1 / -1" in css
    # Closed for the grid on screen, and kept closed when the frame is mounted again.
    assert "note: noteClosed," in JS and "if (ui.note) { noteClosed = true; paintNote(); }" in JS


_BUG = r"""
const window = {};
const navigator = { language: plan.lang };
const document = { documentElement: {} };
""" + I18N_JS + r"""
readLang();
let PEXAFY_HOME = "https://preprod.pexafy.com";
""" + _function("function bugUrl() {") + r"""
process.stdout.write(JSON.stringify(bugUrl()));
"""


@needs_node
def test_the_bug_opens_the_contact_form_on_bug_reports_in_the_reader_s_language():
    def url(lang):
        return _node("const plan = " + json.dumps({"lang": lang}) + ";\n" + _BUG)

    assert url("en") == "https://preprod.pexafy.com/contact/Bug%20reports"
    assert url("fr") == "https://preprod.pexafy.com/fr/contact/Bug%20reports"
    assert url("pt-BR") == "https://preprod.pexafy.com/pt-br/contact/Bug%20reports"
    assert url("xx") == "https://preprod.pexafy.com/contact/Bug%20reports"


def test_the_bug_sits_next_to_the_account():
    html = widget.GRID_HTML
    assert html.index('id="bugb"') < html.index('id="acctwrap"')
    assert 'bugBtn.addEventListener("click", () => openLink(bugUrl(), "bug"));' in JS


_ORDER = r"""
const log = [];
const keptPhotos = new Map(plan.ids.map((id) => [id, { id: id }]));
function keptList() { return Array.from(keptPhotos.values()); }
let selSpoken = false;
function paintSelBlock() { log.push("paint"); }
function pushContextToModel() { log.push("push"); }
function saveUi() { log.push("save"); }
""" + _function("function moveKept(from, to) {") + r"""
const moved = plan.moves.map(([from, to]) => moveKept(from, to));
process.stdout.write(JSON.stringify({ moved: moved, order: keptList().map((f) => f.id),
                                      spoken: selSpoken, log: log }));
"""


@needs_node
def test_a_photo_dropped_elsewhere_takes_its_new_rank():
    out = _node("const plan = " + json.dumps(
        {"ids": ["a", "b", "c", "d"], "moves": [[0, 2], [3, 0], [1, 1], [0, 9]]}) + ";\n" + _ORDER)
    assert out["moved"] == [True, True, False, False]
    assert out["order"] == ["d", "b", "c", "a"]
    # Told at once: the server's copy, the model's context, the view.
    assert out["spoken"] is True
    assert out["log"] == ["paint", "push", "save"] * 2


def test_the_handle_is_the_rank_with_six_dots_and_the_keys_move_it():
    body = _function("function paintSelBlock() {")
    assert '<span class="rk grip" role="button" tabindex="0" data-grip="' in body
    assert 'tx("Drag to change the order.")' in body
    assert 'g.addEventListener("pointerdown"' in body and 'e.key' not in body.split("keydown")[0]
    assert '"ArrowLeft"' in body and '"ArrowRight"' in body and "focusGrip(to)" in body
    # Six dots, two columns of three, as on the try-it page.
    assert JS.count("grip: \"M9 5a1.5 1.5 0 1 1-3 0") == 1
    css = widget._STYLE
    assert "touch-action: none" in css[css.index(".seltile .rk.grip {"):][:400]


def test_the_context_says_when_the_reader_rearranged_the_order():
    """ChatGPT, 2026-10-02: after a drag the assistant still called the list "the order
    you liked them in" — the context said "in the order liked"."""
    ctx = _function("function writeContextToModel() {")
    assert "selReordered ?" in ctx and "not the order liked" in ctx
    assert "selReordered = true;" in _function("function moveKept(from, to) {")
    assert "reordered: selReordered," in JS and "if (ui.reordered) selReordered = true;" in JS
    render = _function("function render(sc) {")
    assert render.index("keptPhotos.clear();") < render.index("selReordered = false;")
