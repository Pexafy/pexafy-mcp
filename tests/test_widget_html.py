"""Facts about the grid widget's HTML that nothing else would catch.

The widget runs in someone else's sandboxed iframe, so the ways it breaks are all
invisible from here: a stylesheet it cannot fetch, a theme it does not follow, a
request to the host that jolts the conversation. Each test below is one of those,
already lived through once.
"""
from __future__ import annotations

import re

from pexafy_mcp import tooling, widget


HTML = widget.GRID_HTML
REDUCED_GUARD = '@media (prefers-reduced-motion: reduce) { .tstep.anchor.hint { animation: none; } }'


def test_the_widget_follows_a_theme_the_host_pushes():
    """`applyDocumentTheme` sets `data-theme` on <html> — a media query alone misses it.

    The detail panel used to be styled only under `@media (prefers-color-scheme: dark)`,
    so a host that runs dark by its own setting, on a machine whose OS is light, got a
    white sheet over a dark grid.
    """
    assert ':root[data-theme="dark"]' in HTML
    assert "prefers-color-scheme: dark" in HTML


def test_a_context_without_a_theme_leaves_the_theme_alone():
    """A host-context notification carries only what changed — a display mode, the
    insets. `applyDocumentTheme` writes its argument into `data-theme` as it is, so one
    called with no theme set `data-theme="undefined"` and the host's explicit dark theme
    was gone until the next notification that happened to carry one."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    assert "applyDocumentTheme(ctx && ctx.theme)" not in js
    calls = [ln for ln in js.splitlines() if "applyDocumentTheme(" in ln]
    assert len(calls) == 2                     # the notification, and connect()
    for line in calls:
        m = re.search(r"applyDocumentTheme\((\w+)\.theme\)", line)
        assert m, line
        who = m.group(1)
        assert f"if ({who} && {who}.theme)" in line, line


def test_the_widget_fetches_nothing_at_runtime():
    """The app sandbox blocks external subresources: everything is inlined or nothing
    renders at all (the SDK, the icons). The typeface is the platform's own, so there
    is none to fetch or to inline (see the next test)."""
    assert "<script src=" not in HTML
    assert "<link " not in HTML
    assert "@import" not in HTML
    assert "url(http" not in HTML


def test_nothing_served_in_the_grid_speaks_of_plans():
    """The frame's own script and styles are served to the host whole, comments
    included, and OpenAI's plugin policy forbids promoting upgrades. Three comments
    said it outright ("long enough for somebody to upgrade", "the only thing left is a
    bigger plan"): the reasoning belongs in the Python beside them, not in what a
    reviewer reads in the frame. The vendored SDK is left out: it is not ours."""
    scripts = re.findall(r"<script(?:\s[^>]*)?>(.*?)</script>", HTML, re.S)
    own = scripts[-1] + "".join(re.findall(r"<style>(.*?)</style>", HTML, re.S))
    assert "function learnLinks(meta)" in own          # the frame's script, not the SDK
    words = re.compile(r"upgrad|\bplans?\b|pricing|\bprices?\b|\bpaid\b|\bbuy|"
                       r"purchas|subscri|checkout|upsell|premium|\bsell|unlimited|credits",
                       re.I)
    found = [own[max(0, m.start() - 40):m.end() + 20] for m in words.finditer(own)]
    assert not found, found


def test_the_grid_writes_in_the_platform_s_own_typeface():
    """OpenAI's UI guidelines: "Don't use custom fonts, even in full screen modes." The
    grid carried Inter, inlined as base64 — 178 KB of every widget served, for a
    typeface the host had not chosen. It uses the system stack now, and inlines no
    font at all."""
    assert "@font-face" not in HTML and "data:font/" not in HTML and "woff" not in HTML
    assert "'Inter'" not in HTML and '"Inter"' not in HTML
    font = HTML[HTML.index("--font:"):]
    font = font[:font.index(";")]
    assert font.split(":", 1)[1].split()[0] == "system-ui,"
    assert font.rstrip().endswith("sans-serif")
    # Every text of the grid goes through the token: no family is named elsewhere.
    families = re.findall(r"font-family:\s*([^;}]+)", HTML)
    assert families and all(f.strip() == "var(--font)" for f in families), families


def test_full_screen_is_asked_for_on_a_phone_and_nowhere_else():
    """`fullscreen` on a DESKTOP was tried and taken back out: coming back to `inline`
    makes the host re-lay out the thread, and ChatGPT answers that by scrolling the
    reader to the TOP of the conversation. Every photograph closed threw away their
    place in it, and no close path avoids it — the mode change itself is what does it.

    A phone is the other case: inline, the swipe deck gets the height of a postcard on a
    screen held in one hand. So the request exists, and `isPhone()` is the only door to
    it — `goFull()` returns before asking on every other surface."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    assert "app.requestDisplayMode({ mode: \"fullscreen\" })" in js
    # The single gate, and it is a gate: the guard returns before the request.
    assert "if (wentFull || !deckOpen || !isPhone() || !canGoFull()) return false;" in js
    assert js.count("requestDisplayMode({ mode: \"fullscreen\" })") == 1
    assert "setDisplayMode" not in js            # a request, never an order


def test_the_phone_is_the_hosts_word_and_never_a_user_agent():
    """`platform` is what the host calls its own surface ("web"/"desktop"/"mobile"),
    and it rides in the same host context the theme does. Sniffing `navigator.userAgent`
    for "iPhone" is how a widget ends up full screen on a desktop browser someone has
    narrowed, and wrong about every host that ships next."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    assert "p === \"mobile\"" in js
    assert "navigator.userAgent" not in js or "platform" in js
    assert "/iPhone|Android/" not in js


def test_full_screen_is_given_back_before_the_grid_is_measured_again():
    """The grid, the shortlist strip and the foot are all measured on the way out of the
    deck, and they have to be measured inline — a widget that repaints them while the
    host still has it at screen height hands the thread a box the size of a phone."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    close = js[js.index("function closeViewer()"):]
    assert close.index("leaveFull();") < close.index("paintSlot();")


def test_the_widget_follows_the_host_out_of_full_screen_without_closing_the_photo():
    """The reader can leave full screen with the host's own control, which no button of
    ours hears about — so the widget follows `displayMode` and repaints.

    What it must NOT do is close the photograph on the strength of a notification. It
    did, and that is the "it opens full screen and vanishes again" bug: a host context
    carrying the state from BEFORE the request (in flight when we asked, or a frame of
    the transition) read exactly like the reader leaving. Two guards now: a mode
    notification only ever repaints, and an unconfirmed `inline` inside FULL_ECHO_MS of
    the grant is discarded as the echo it is."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    assert "syncDisplayMode(ctx)" in js
    assert "ctx && ctx.displayMode" in js
    assert "const FULL_ECHO_MS = 1800;" in js
    assert "if (!fullSeen && Date.now() - fullAt < FULL_ECHO_MS) return;" in js
    # The body of the sync function closes nothing.
    body = js[js.index("function syncDisplayMode(ctx) {"):]
    body = body[:body.index("\n}\n")]
    assert "closeViewer" not in body


def test_the_deck_survives_the_frame_being_reloaded_under_it():
    """The measured cause of "full screen opens and vanishes, every other time".

    A host is free to move the widget's iframe in its own DOM, and promoting an inline
    widget into a full-screen container is exactly that. Moving an iframe RELOADS it:
    the document is destroyed and parsed again, so the deck, the photograph on top and
    the shortlist are gone a heartbeat after the screen was granted. Reproduced in
    grid_lab ("Remount on fullscreen"): four openings, four losses; with this state
    written and read back, four openings, four decks.

    Nothing inside the frame outlives it — `sessionStorage` and `localStorage` both
    throw SecurityError under the app sandbox's opaque origin, and `window.name` does
    not survive a re-parent; both measured. The host's own store is the only one that
    does."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    assert 'const UI_KEY = "pexafyView";' in js
    assert "function restoreUi()" in js and "function saveUi(now)" in js
    # Written the instant the deck opens, because goFull() is the next thing that runs
    # and the host may answer it by destroying this document. A coalesced write here is
    # a write that never happened.
    assert "saveUi(true);" in js
    open_body = js[js.index("function openViewer(i, list) {"):]
    open_body = open_body[:open_body.index("\n}\n")]
    assert open_body.index("saveUi(true);") < open_body.index("goFull()")
    # Read once per frame, and only onto the same answer it was written over.
    assert "if (uiRestored) return;" in js
    assert "if (ui.sig && ui.sig !== resultSig()) return;" in js
    assert "Date.now() - ui.at > UI_TTL_MS" in js
    # And neither writer tramples the other: the store is composed from parts, each
    # owned by one caller, seeded once from what the host holds.
    assert "function storePut(patch, now)" in js
    assert "if (!storeParts) storeParts = partsOf(hostState());" in js
    assert "function rememberView(view, now)" in js
    save_ui = js[js.index("function saveUi(now)"):]
    save_ui = save_ui[:save_ui.index("\n}\n")]
    assert "rememberView(view, now);" in save_ui
    assert "UI_KEY" not in save_ui                # where the view goes is the store's call


def test_the_view_state_is_written_before_the_screen_is_handed_back():
    """The other half of "closing it redoes my search". Giving the screen back is a mode
    change, and a host that promotes a widget into a full-screen container re-parents
    the iframe BOTH ways — so the document can be destroyed inside leaveFull(). A state
    written after that is a state never written, and the frame came back up reading
    "the deck was open" and opened it again on top of the grid."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    body = js[js.index("function closeViewer() {"):]
    body = body[:body.index("\n}\n")]
    assert body.index("saveUi(true);") < body.index("leaveFull();")


def test_a_result_the_widget_already_has_is_not_a_new_question():
    """The measured cause of "like a photo, star another one, and it all starts over".

    A host may hand the answer to a component's own `callTool` straight back to that
    component as a fresh tool result — the same channel the assistant's first search
    arrives on. From inside the frame the two are indistinguishable except by their
    contents, and render()'s reset is brutal by design: trail emptied, shortlist
    cleared, back to the anchor. Reproduced in grid_lab ("Re-render after callTool"):
    after the star, trail 0 and nothing kept.

    Recognised by the photographs themselves, which cannot lie: a payload whose ids are
    a page already in the trail IS that page. The account corner still takes it — a
    refinement really did spend a call."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    assert "function payloadSig(sc)" in js
    assert "if (psig && trail.some(p => p.psigs.indexOf(psig) !== -1)) {" in js
    # Both kinds of page carry it, or the check only half works — and both are built by
    # the same function, so neither can be given the field and the other forgotten.
    assert "trail.push(newPage(null, built, sc));" in js
    assert "trail.push(newPage(f, built, sc));" in js
    assert "psigs: sig ? [sig] : []," in js
    # A LIST, not one signature. A page wears a second one the moment the shape filter
    # re-asks its question: same step, different photographs, different payload. Left at
    # a single value, the host handing that answer back as a tool result would read as a
    # new question and empty the trail — the exact bug this test is named after, moved
    # from the star to the filter.
    assert "if (sig && here.psigs.indexOf(sig) === -1) here.psigs.push(sig);" in js
    # And the early return happens BEFORE anything is thrown away.
    body = js[js.index("function render(sc) {"):]
    body = body[:body.index("\n}\n")]
    assert body.index("trail.some(p => p.psigs.indexOf(psig) !== -1)") < body.index("kept.clear();")


def test_the_refinements_travel_with_the_view_state():
    """A refined page exists ONLY in this frame: the widget called the tool itself, so
    the host is holding the original result and nothing else. Reloaded without this, a
    reader who had starred twice was dropped back on the search they started from —
    which is what "the star redoes my search" looked like."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    assert "trail: trimTrail()," in js and "step: step," in js
    assert "function trimTrail(budget)" in js
    assert "const TRAIL_BUDGET = 192 * 1024;" in js          # a ceiling, not a cliff
    # Restored before anything that depends on which page is on screen.
    body = js[js.index("function applyView(ui, fromServer)"):]
    body = body[:body.index("\n}\n")]
    assert body.index("ui.trail") < body.index("ui.shown")
    assert body.index("ui.trail") < body.index("ui.pid")
    # The anchor is the host's own, freshly signed; only the refinements are replayed.
    assert "for (const pg of saved.slice(1))" in js
    # With one exception, and it is the shape filter: a filtered anchor was fetched by
    # the frame, exists nowhere else, and the host is about to send the UNfiltered
    # search back. Left out, a reader who filtered to portraits and whose host then
    # remounted the iframe watched the landscapes return and the button switch itself
    # off. `base` still comes from the host, so "every shape" goes back to fresh links.
    assert "const anchorFiltered = !!(savedAnchor && normShapes(savedAnchor.shape).length" in js
    assert "anchor.photos = savedAnchor.photos;" in js
    assert "if (saved.length > 1 || anchorFiltered) {" in js
    # Every page goes out packed and comes back adopted. The pack drops `base` — the
    # unfiltered set the shape filter keeps beside the filtered one — whenever it IS
    # `photos`, which is every page nobody filtered: without that a six-page trail
    # serialised each photograph twice and pages started falling off the front for a
    # feature most readers never touch. The adopt defaults every field a page written by
    # an older frame does not carry; `psigs` is the sharp one, read by render() on every
    # tool result.
    assert "function packPage(pg)" in js and "function adoptPage(pg)" in js
    assert "if (pg.base && pg.base !== pg.photos) out.base = pg.base;" in js
    assert "pg.psigs = Array.isArray(pg.psigs) ? pg.psigs : [];" in js
    assert "trail.push(adoptPage(pg));" in js
    # The anchor's photographs are the host's own and come back with the next result:
    # stored only when the reader filtered them in here, which is the one case the
    # restore reads them. Unfiltered, the slot is a stub — without it a state with no
    # refinement weighed a whole page (~16 KB) for nothing. The stub keeps what the
    # answer's `_meta` said — its link, and the allowance wording with the payload it
    # belongs to — since a reloaded frame may not be handed that `_meta` again.
    assert "let out = trail.map((pg, i) => (i ? packPage(pg) : packAnchor(pg)));" in js
    anchor = js[js.index("function packAnchor(pg) {"):]
    anchor = anchor[:anchor.index("\n}\n")]
    assert "const stub = { shape: [], cta: cta, psig: (pg.psigs && pg.psigs[0]) || \"\" };" in anchor
    assert "if (pg.wording) stub.wording = pg.wording;" in anchor
    # Filtered: exactly what the restore takes from it — never `base`.
    for field in ("photos: pg.photos", "cta: pg.cta", "baseCta: cta",
                  "wording: pg.wording || null", "shape: shapeList(pg.shape)",
                  "origin: pg.origin || null", "psigs: pg.psigs || []"):
        assert field in anchor, field
    assert not re.search(r"\bbase\b", anchor)


def test_a_full_screen_already_granted_is_adopted_not_asked_for_again():
    """A frame that reloaded inside a full-screen container comes up with the host
    already in the mode it is about to request. Asking again is a round trip whose only
    answer is "you have it" — and a mode change the host may animate on the way."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    assert 'if ((hostCtx() || {}).displayMode === "fullscreen") {' in js
    block = js[js.index("async function goFull() {"):]
    block = block[:block.index("\n}\n")]
    assert block.index('displayMode === "fullscreen"') < block.index("app.requestDisplayMode")


def test_the_deck_does_not_scroll_the_thread_it_is_about_to_leave():
    """`scrollIntoView` reaches out of the iframe and scrolls the HOST. On a phone a
    scroll of the thread arriving in the same breath as the switch to full screen is how
    a reader dismisses a full-screen app — the widget was shoving the host at the moment
    the host was opening, which is the other half of the vanishing full screen. In full
    screen there is nothing to scroll into view: the deck IS the surface. So the scroll
    waits for the host's answer and only happens if the answer is no."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    block = js[js.index("  if (isPhone() && canGoFull()) {"):]
    block = block[:block.index("\n}\n")]
    assert "goFull().then(ok => { if (!ok && deckOpen) reveal(viewerEl, \"center\"); });" in block
    assert "} else {\n    reveal(viewerEl, \"center\");" in block


def test_the_notch_comes_from_the_host_not_from_env():
    """`env(safe-area-inset-*)` measures the IFRAME's box, which already sits inside the
    inset area, so it reports zero and the photograph runs under the notch. The host
    reports the real insets in its context."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    assert "safeAreaInsets" in js
    assert "--safe-t" in js and "--safe-b" in js
    assert "padding: var(--safe-t, 0px) var(--safe-r, 0px)" in widget.GRID_HTML
    assert "#viewer.vfull" in widget.GRID_HTML
    assert "100dvh" in widget.GRID_HTML          # a phone's address bar slides away


def test_thumbnails_only_ever_come_from_the_signed_pexafy_cdn():
    """The resource CSP names one image origin. A provider URL used as a fallback does
    not degrade — it paints a broken glyph with its alt text sprawled over the grid."""
    assert "const thumb = link.small || thumbUrl(photo.preview_url);" in widget._WIDGET_JS
    assert "large: link.large || thumbUrl(photo.preview_url_large) || thumb," in widget._WIDGET_JS
    assert not re.search(r'preview_url(_large)?\)? \|\| u\.(small|thumb|regular)', widget._WIDGET_JS)


def test_the_way_out_says_where_it_goes_and_nothing_about_the_grid():
    """The product's name sits at the top left, where a product puts it — in plain
    text, not the site logo's gradient (see test_no_logo_and_no_custom_gradient).

    The button at the other end said "Full results". The grid shows sixteen results
    and no more, so the label read as a plugin holding back the rest of an answer —
    and OpenAI's guidelines ask a plugin not to offer a lesser version of the product.
    It says where it goes: "Open in Pexafy". The server's label (`cta`) says the same
    for the wordmark's tooltip."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    assert "brandmark" in HTML
    assert "background-clip" not in HTML
    assert '<span>\' + esc(tx("Open in Pexafy")) + "</span>"' in js
    assert "Full results" not in HTML
    slot = js[js.index("function paintSlot()"):js.index("/* ── Who is asking")]
    # The markup, not the notes: block comments across lines, line comments to the end
    # of their line only (`//.*` under re.S would take the rest of the function).
    slot = re.sub(r"//[^\n]*", "", re.sub(r"/\*.*?\*/", "", slot, flags=re.S))
    assert "brandcta" in slot and "moreb" in slot       # the function is all there
    assert slot.count("Pexafy") == 1 and "wordmark" not in slot
    # The server's label, or the same words, is what the wordmark says it leads to.
    assert 'footLabel = "Open in Pexafy"' in js
    assert "brandEl.title = tx(footLabel);" in js


def test_the_deck_follows_the_theme_like_the_grid_does():
    """The viewer used to be dark whatever the host asked for: a white grid that turned
    black the moment a photo was opened. Every colour inside #viewer now comes from a
    --v-* token, defined light on bare :root and dark in the two dark selectors, so the
    deck reads the theme the same way the grid does."""
    assert "--v-ink:" in HTML and "--v-back:" in HTML
    # The two dark selectors carry the viewer palette as well as the grid one.
    assert HTML.count("--v-back:") >= 3          # :root + media query + [data-theme]
    viewer_css = HTML[HTML.index("#viewer { position:"):HTML.index("/* ── Toast")]
    # The chrome — background, ink, progress bar, button plates — is all tokens now.
    # What stays hard-coded white is only what sits ON a photograph (the swipe stamps
    # and the credit overlay, both over their own scrim): that is a contrast
    # requirement against an unknown picture, not a theme choice.
    chrome = viewer_css[:viewer_css.index(".vinfo {")]
    for hard in ("rgba(255,255,255,.66)", "rgba(255,255,255,.42)", "#0b0b10"):
        assert hard not in chrome, hard


def test_rewind_is_a_position_not_an_undo_stack():
    """It used to be `.vundo`, disabled until something had been swiped — greyed out on
    a freshly opened deck, which is exactly when a reader who tapped the wrong card
    wants it, and unreachable for someone who entered the deck on card #7. It is now
    driven by the index alone and stops only at the first photo."""
    assert 'querySelector(".vbig.rewind").disabled = idx <= 0' in widget._WIDGET_JS
    assert "function rewind()" in widget._WIDGET_JS
    assert "undoStack.length === 0" not in widget._WIDGET_JS


def test_swiping_up_asks_for_similar_photos():
    """The gesture and the ≈ button are the same action — 'more like this one' — and
    both put a turn in the thread carrying the photo_id."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    assert "if (dy < 0) superLike(); else closeViewer();" in js
    assert 'querySelector(".vbig.sup").addEventListener("click", superLike)' in js
    assert 'Find me more photos like this one' in js and 'photo_id "' in js
    # `journey()` was called here and defined nowhere: on the only hosts that reach
    # this line it raised before the turn could be spoken.
    assert "journey()" not in js


def test_the_trail_shows_what_each_stop_was_worth():
    """First readers did not find the way back: the trail sat against the wordmark and
    read as part of the logo. It is centred in its own band now, every chip carries the
    number of its photos on the shortlist, and the anchor beats three times when the
    trail grows — then holds still, because a control that pulses forever is an alarm."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    assert "function likedIn(n)" in js and "function countBadge(n)" in js
    assert ".tn {" in HTML and "@keyframes anchorbeat" in HTML
    assert ".tstep.anchor.hint" in HTML
    assert "justify-content: safe center" in HTML
    # Centred on the WHOLE width, not on the strip left over beside the wordmark:
    # off-centre it read as an ornament hanging off the logo.
    head = HTML[HTML.index(".gridhead { display:"):HTML.index(".brandmark {")]
    assert "grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr)" in head
    assert "grid-column: 2" in HTML and "justify-self: center" in HTML
    # `overflow-x: auto` makes the OTHER axis `auto` too, which sheared the flat tops
    # off the counts. The padding has to be wider than the badge hangs, on every side.
    trail = HTML[HTML.index(".trail { grid-column:"):HTML.index(".trail::-webkit-scrollbar")]
    assert "padding: 9px 9px 5px" in trail          # the badge hangs 6px
    # The picture is clipped by a box of its own; rounding the <img> to the button's
    # inner curve left a hair of photo outside the frame on every chip.
    assert ".tw { display: block;" in HTML and "overflow: hidden" in HTML
    assert '<span class="tw">' in js
    # The beat fires when the trail GROWS, not on every repaint.
    assert "function beatTheAnchor()" in js
    assert "beatTheAnchor();" in js[js.index("async function refineFrom(f)"):
                                    js.index("/* ── The deck ")]
    assert "beatTheAnchor" not in js[js.index("function showStep()"):js.index("function goToStep(n)")]
    # A heart moves a count without rebuilding the trail — that would restart the beat.
    assert "function paintTrailCounts()" in js
    assert "paintTrailCounts();" in js[js.index("function setKeptPhoto(f, on)"):
                                       js.index("function setKept(i, on)")]
    assert REDUCED_GUARD in HTML


def test_the_model_is_told_what_the_user_did_on_two_channels():
    """`ui/update-model-context` is the portable way and it is not reliable where it
    matters: ChatGPT serves the model a STALE selection — the view reports the update as
    sent and a later turn still names the previous one
    (openai/openai-apps-sdk-examples#221, open, no workaround offered). `setWidgetState`
    is ChatGPT's own store, persisted with the conversation, and `modelContent` is the
    half of it the model is shown. Both are written; they can only disagree when the
    buggy one is wrong."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    assert "function rememberForModel(text, sel)" in js
    body = js[js.index("function rememberForModel(text, sel)"):
              js.index("/* ── Surviving a reload of the frame")]
    # Its own part of the store and nothing else: setWidgetState writes the object
    # WHOLE, and the view state lives in the same one.
    assert "storePut({ text: text, sel: sel });" in body
    # …and exactly one place calls the host, so two writers cannot drop each other.
    assert js.count("oai.setWidgetState(") == 1
    # The text goes out as `modelContent`, the half of the store the model is shown.
    write = js[js.index("function storeWrite()"):js.index("function storePut(patch, now)")]
    assert 'modelContent: storeParts.text == null ? "" : storeParts.text,' in write
    # The widget-state channel is written even where the portable one is unavailable.
    push = js[js.index("function writeContextToModel()"):js.index("function setKeptPhoto(f, on)")]
    assert push.index("rememberForModel(") < push.index('hostCan("updateModelContext")')
    # The context names the actor, so a host cannot read it as the assistant's own finding.
    # The shortlist itself moved to a tool; what the context keeps is its COUNT.
    assert "Liked by the reader: " in js


def test_a_turn_in_the_thread_goes_through_the_host_that_can_take_one():
    """`ui/message` is the protocol way and Claude honours it. ChatGPT rendered it as
    a message from the ASSISTANT and then did nothing — the text landed in the thread
    with the assistant's own reply controls under it and no tool was ever called. Its
    own surface, `window.openai.sendFollowUpMessage`, inserts a USER turn and runs it,
    so it is tried first where it exists; `ui/message` remains the fallback."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    assert "function speak(text)" in js
    assert "sendFollowUpMessage" in js
    assert js.index("sendFollowUpMessage") < js.index('app.sendMessage({ role: "user"')
    # Every path that speaks goes through it — no second, divergent copy.
    assert js.count("app.sendMessage(") == 1


def test_there_is_no_choose_this_one_button():
    """A tick sat in the middle of the verb row, one button from the heart, and the two
    are not the same kind of act at all: the heart adds a photograph to a shortlist the
    reader can still change, the tick ENDED the session — it spoke in the thread, threw
    away everything else they had kept, and did it on a glyph that says nothing more
    specific than "yes". A reader reaching for the heart and landing one over had their
    whole selection replaced by a single photo.

    One way out, and it is the one they can see: keep what you want, then "Use in chat"
    under the strip, where the count is. The place it vacated went to `download`."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    assert "sendCurrent" not in js and "vbig send" not in HTML
    assert "ICON.choose," not in js              # the 26px tick had no other user
    assert "vbig get" in HTML
    assert 'querySelector(".vbig.get").addEventListener' in js
    # One place speaks, and it is the shortlist's own button.
    # Two callers left — the shortlist, and asking for more like one.
    assert js.count("if (speak(text))") == 1   # only the star's fallback speaks now


def test_the_photo_fills_the_card_and_the_words_sit_on_it():
    """`contain` left the picture small and floating in a pale wash. The card is one
    picture edge to edge, and the credit is an overlay on it that swipes away with it."""
    assert "object-fit: cover" in HTML
    assert ".vinfo" in HTML and "linear-gradient(transparent, rgba(0,0,0,.30)" in HTML
    assert "function metaHtml(f)" in widget._WIDGET_JS
    # The overlay lives inside the top card, not beside the deck.
    assert '<div class="vinfo">' in widget._WIDGET_JS


def test_the_verbs_are_opaque_plates():
    """Translucent chips washed out to nothing over a light photo — which is precisely
    where they had to be readable. The plate is opaque and the glyph carries the colour."""
    assert "--v-btn:" in HTML
    assert "background: var(--v-btn)" in HTML


def test_the_deck_is_one_card_and_nothing_behind_it():
    """Two blank plates used to sit behind the top card to read as a deck. At rest the
    photo covered them exactly; the only moment they ever showed was mid-throw, as a
    bare white rectangle with a shadow on a white ground. A depth cue you can only see
    when it looks broken is not a depth cue — the card that follows is drawn for real
    instead, as this one leaves."""
    assert ".vplate" not in HTML and "--pw" not in HTML
    assert "vcard b1" not in widget._WIDGET_JS
    assert "function sizeCards()" in widget._WIDGET_JS
    # …and the card follows a host resize rather than freezing at its first measurement.
    assert "ResizeObserver" in widget._WIDGET_JS


def test_the_photograph_shows_the_gesture_by_making_it():
    """Nothing on screen tells a first-time reader the picture can be thrown. It was two
    chevrons on the left and right edges for a while, and they taught the wrong thing: a
    round button with an arrow in it over a photograph is a carousel, so readers pressed
    them and nothing happened. The only honest way to show a gesture is to make the thing
    do it — the card swings, damped, and settles.

    It was here before and was taken out because the card used to have pale plates
    stacked behind it and the swing dragged one into view as a bare slab. The plates are
    gone; there is nothing behind the card now but the ground."""
    assert ".vcoach" not in HTML and "@keyframes coachfade" not in HTML
    assert "@keyframes sway" in HTML
    # Never while it is being dragged: an animation outranks the inline transform the
    # drag writes, so a sway still running would fight the thumb.
    assert ".vcard.top.sway:not(.drag)" in HTML
    assert "if (!coached && !REDUCED)" in widget._WIDGET_JS
    assert "function stopCoaching()" in widget._WIDGET_JS
    # Motion is opt-out, not mandatory.
    assert "prefers-reduced-motion" in HTML and "REDUCED" in widget._WIDGET_JS


def test_the_super_like_is_celebrated():
    """The one moment the widget cheers: a blue star blooms over the deck as the photo
    leaves upwards, so finding the one worth chasing feels like finding it."""
    assert "@keyframes burst" in HTML and ".vburst" in HTML
    assert "function burstStar()" in widget._WIDGET_JS
    assert "--act-sup" in HTML


def test_the_four_verbs_are_colour_coded():
    """Four round glyphs with no legend: colour is what tells them apart, and it is the
    colour scheme the gesture was learned in — amber rewind, red skip, blue star, green
    keep."""
    for token in ("--act-rewind", "--act-pass", "--act-sup", "--act-keep"):
        assert token in HTML, token
    for cls in ("vbig rewind", "vbig pass", "vbig sup", "vbig keep"):
        assert cls in HTML, cls


def test_the_card_keeps_the_photo_s_own_shape():
    """One landscape frame for every photo cropped a 3914×5871 portrait to a letterbox,
    which misrepresents the thing being searched for. CSS alone cannot fit a box to a
    ratio with both axes bounded — `width:100%` + `max-height` clips the height and
    keeps the width, `height:100%` + `max-width` does the mirror — so the box is
    measured and written in pixels."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    assert "function fitBox(ar, dw, dh)" in js
    assert "function sizeCards()" in js
    assert 'shot.style.width = box[0] + "px"' in js
    # …from the payload's own dimensions, with the file itself as the fallback.
    assert "w: w || 0, h: h || 0," in js
    assert "function fixRatio(img)" in js


def test_a_swipe_shows_the_next_photo_rather_than_a_smear():
    """Mid-swipe the reader used to see a 55%-opaque plate wearing a cropped copy of the
    next photo, which then snapped into the real thing. The next card is now built for
    real and faded up as the top one leaves, the plates step aside, and the file is
    already in cache because it was fetched while the previous photo was on screen."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    assert "function revealNext()" in js
    assert "function prefetch(list, i)" in js and "prefetch(DECK, idx + 1)" in js
    assert ".vcard.nx" in HTML and ".vcard.nx.up" in HTML


def test_asking_for_similar_photos_leaves_the_photo_where_it_is():
    """The star used to throw the card off the deck and close the viewer, so the badge
    appeared to land on whatever photo came next. Asking for more like this one is a
    request, not a verdict on the picture: it stays, wearing the star."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    assert "function starredHere()" in js and ".vstar" in HTML
    assert "function markStar()" in js
    # No advance, no fly-out, no close on the super-like path.
    body = js[js.index("function superLike()"):js.index("// Pointer drag:")]
    assert "revealNext()" not in body
    assert "idx++" not in body
    assert "closeViewer()" not in js[js.index("function askSimilar()"):js.index("/* ── Rendering a tool result")]


def test_a_skip_drops_a_photograph_the_reader_is_holding():
    """Keep a photo from the deck, come back to it, press Nope — and it stayed in the
    strip. The cross was a lie, and there was no way to undo a heart from the deck the
    grid opens. Dropping used to happen only in a review pass over the selection, and
    what a button MEANS cannot depend on which door the reader came through.

    A skip on a photo that was never kept still decides nothing."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    body = js[js.index("function decide(keep, dir)"):js.index("function rewind()")]
    assert "else if (isKept(idx)) setKept(idx, false);" in body
    assert "reviewing" not in body               # the mode no longer changes the verb


def test_the_shortlist_is_a_grid_under_the_results_not_a_number():
    """It was a chip on the footer line — "6 images selected", a cross and a tick. A
    number is the one thing that cannot answer the question the reader has at that
    moment, which is *which* six: they have just spent a minute looking at photographs,
    and the chip shows them none.

    The shortlist is a second grid under the results now. Every kept photograph is on
    screen, each with the red cross that drops it, and pressing one re-opens the deck
    over the selection alone."""
    assert "selbar" not in HTML and 'id="picked"' not in HTML
    assert 'id="selblock"' in HTML
    assert ".selgrid" in HTML and ".seltile" in HTML
    assert ".ico.clear" in HTML
    js = widget._with_tool_names(widget._WIDGET_JS)
    body = js[js.index("function paintSelBlock()"):js.index("/* ── The trail")]
    # A tile per photo, its own cross, and the way back into the deck.
    assert 'class="selgrid"' in body
    assert 'data-drop="' in body and 'data-open="' in body
    assert "openViewer(at, keptList())" in body


def test_the_way_out_names_its_destination():
    """"More at Pexafy" under a downward arrow promised "scroll"; the grid is the first
    page of a much larger answer and the link is where the rest of it lives. A noun
    phrase, and an arrow that points the way out."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    assert 'footLabel = "Open in Pexafy"' in js
    cta = js[js.index("moreEl.innerHTML = '<button class=\"brandcta\""):]
    cta = cta[:cta.index("\n}\n")]
    assert "ICON.down" not in cta         # "scroll" is not what this button promises
    assert 'ICON.right + "</span></button>"' in cta
    assert ".brandcta:hover .arrow { transform: translateX(3px); }" in HTML


def test_the_deck_sits_on_the_same_white_the_grid_does():
    """Opening a photo used to drop a grey panel over a white conversation. Under a light
    theme the ground is plain white — the blurred copy of the photo is a dark-theme
    effect — and clicking that empty ground closes the deck."""
    assert "--v-back: #ffffff" in HTML
    assert "--v-blur-o: 0;" in HTML
    js = widget._with_tool_names(widget._WIDGET_JS)
    assert 't.closest(".vctl, .vtop")' in js
    # …and a throw that springs back is not a tap on the background — measured as the
    # furthest the gesture got, because one that comes back to where it started ends at
    # an offset of zero and used to close the deck under the reader.
    assert "if (dragMoved > 6) return;" in js
    assert "dragMoved = Math.max(dragMoved, Math.abs(dx) + Math.abs(dy));" in js


def test_the_gestures_are_not_captioned():
    """A line of small grey text under the buttons explaining the four swipes only says
    the design failed. The deck and its five verbs carry it."""
    assert "vhint" not in HTML
    assert "Swipe right to keep" not in HTML


def test_the_credit_takes_the_line_the_pixel_count_was_using():
    """Nobody chooses a photograph by its pixel dimensions — they are on the photo page,
    beside the download button, which is where that question gets asked. The credit moves
    up next to the rank."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    body = js[js.index("function metaHtml(f)"):js.index("function paintMeta(f)")]
    assert "f.resolution" not in body
    assert "creditHtml(f)" in body and "class=\"tag rk\"" not in body


def test_the_shortlist_can_be_pruned_where_it_lives():
    """Dropping one that no longer belongs used to be possible only on the deck's
    summary screen — which means only by swiping to the end of the results. The crosses
    are on the selection block under the grid, where the photos are always visible, and
    the cross does NOT open the deck under it."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    assert 'data-drop="' in js
    assert 'querySelectorAll("[data-drop]")' in js
    assert ".seltile .x" in HTML
    body = js[js.index("function paintSelBlock()"):js.index("/* ── The trail")]
    assert "e.stopPropagation();" in body


def test_the_model_context_is_short_and_names_the_tool():
    """It used to carry the whole shortlist twice — prose and JSON — plus the grid's ids
    twice, the refinement twice, and three paragraphs about where a selection lives.
    Every word of that is read on every turn: a model spending its attention on our
    plumbing is a model not spending it on the person's question.

    What is left is what only the widget knows: which grid is on screen, that the tiles
    carry no numbers, and how many photographs the reader has picked — plus the name of
    the tool that hands them over in full. With the tool switched off there is no second
    channel, so the selection itself goes back into the context."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    ctx = js[js.index("function writeContextToModel()"):js.index("function setKeptPhoto(f, on)")]
    # The plumbing prose is gone.
    for gone in ("USER SELECTION IS AUTHORITATIVE", "Enumerate EVERY entry",
                 "THE USER'S CURRENT SELECTION IS pexafy_selection BELOW",
                 "pexafy_selection: selectionData()", "selected_ranks",
                 "selection: {", "grid: {"):
        assert gone not in ctx, gone
    # What stays is one fact per key, each of which the model cannot get anywhere else.
    for kept in ("selection_count: list.length,", "selected_photo_ids:",
                 "selection_tool: tool || null,", "grid_photo_ids:",
                 "refined_from_photo_id:"):
        assert kept in ctx, kept
    assert "tiles carry no numbers" in ctx
    # The tool is named as what returns the photographs — a fact, not an order to call it.
    assert '"; " + tool + " returns them with their details."' in ctx
    assert "Call \" + tool" not in ctx
    # …and the fallback, for a server with the tool switched off, or out of the frame's
    # reach (test_widget_selection_fallback).
    assert 'const tool = selCap && !selPostFailed && selCap.tool;' in ctx
    assert '(tool || !list.length ? "" : "\\n\\n" + selectionText())' in ctx


def test_the_heart_is_on_screen_without_a_pointer():
    """It was `opacity: 0` until the tile was hovered, and a phone has no hover: the
    heart did not exist there. Readers said so — they could not find how to keep a
    photograph because there was nothing to find. A control that appears only to a
    mouse is a control half the readers do not have."""
    css = widget.GRID_HTML
    block = css[css.index("  .card .pick {"):css.index("  .card .pick:hover")]
    assert "opacity: 1" in block and "opacity: 0;" not in block
    # Hover now adds emphasis instead of existence.
    assert ".card:hover .pick, .card:focus-within .pick { transform: scale(1.08); }" in css


def test_the_grid_teaches_three_things_in_order():
    """Three things readers did not discover on their own, all measured on real people:
    that a tile opens, that ≈ fetches more like it, and that the heart tells the
    assistant which ones they want. A photograph looks like a photograph, so the grid
    says it — one line naming the NEXT thing, the control it names lit, and the same
    sentence over the deck's own row when the deck is where it happens.

    Each step is ticked the moment it is done, wherever it was done and in any order,
    and the whole thing is remembered with the view state so it never comes back in the
    same conversation."""
    js = widget._WIDGET_JS
    assert 'const COACH_STEPS = ["tap", "sim", "like"];' in js
    assert "function paintCoach()" in js and "function coachDid(what)" in js
    assert "const finished = coachFinished();" in js
    # The three sentences, on the surface each is said on — the heart's says what
    # pressing it does, not only where to press.
    assert '"Tap a photo to open it"' in js
    assert "for more photos like this one" in js and "for more like it" in js
    assert "to like a photo — the assistant can then use it" in js
    assert "or swipe right to like it — the assistant will know" in js
    # Ticked where the act happens: opening (before the deck paints its own line), the
    # star — button or swipe, both go through superLike — and a heart anywhere.
    open_fn = js[js.index("function openViewer(i, list)"):js.index("/* ── Fitting a photograph")]
    assert open_fn.index('coachDid("tap");') < open_fn.index("paintDeck();")
    star = js[js.index("function superLike()"):js.index("// Pointer drag:")]
    assert 'coachDid("sim");' in star
    kept = js[js.index("function setKeptPhoto(f, on) {"):]
    assert 'if (on) coachDid("like");' in kept[:200]
    # In any order: the line names the first step not yet done.
    nxt = js[js.index("function coachNext()"):js.index("function coachState()")]
    assert "for (const s of COACH_STEPS) if (!coach[s]) return s;" in nxt
    # Closing the deck brings the line back for whatever is next.
    close = js[js.index("function closeViewer()"):js.index("/* The markup of one photo card")]
    assert "paintCoach();" in close
    # The cross ends it at any step, and it is remembered with the view state.
    assert 'esc(tx("Got it"))' in js and "() => coachOff()" in js
    assert "coach: coachState()," in js
    assert 'if (ui.coach && typeof ui.coach === "object")' in js
    assert "tipDone" not in js           # a view that old is past UI_TTL_MS anyway


def test_what_the_coach_points_at_blinks_without_end():
    """Asked for in so many words: the step's dot, the tile, the heart and the deck's
    verb BLINK, for as long as they are the step. The dot used to beat three times and
    stop; the tile's ring held still with a faint halo growing off it; the verb shared
    that halo, painted over its own shadow on a filled plate — barely there."""
    css = widget._STYLE
    def rule(sel):
        at = css.index(sel + " {")
        return css[at:css.index("}", at)]
    assert "@keyframes stepblink" in css and "stepblink 1.1s ease-in-out infinite" in rule(".gridtip .st.now")
    assert "@keyframes coachblink" in css and "coachblink 1.1s ease-in-out infinite" in rule(".card.coach")
    assert "@keyframes pickblink" in css and "pickblink 1s ease-in-out infinite" in rule(".card .pick.coach")
    assert "@keyframes verbblink" in css and "verbblink 1s ease-in-out infinite" in rule(".vbig.coach::after")
    # The ring on the verb is OUTSIDE it, three pixels thick, and on/off — not a halo.
    after = rule(".vbig.coach::after")
    assert "inset: -7px" in after and "border: 3px solid var(--primary)" in after
    blink = css[css.index("@keyframes verbblink"):]
    assert "opacity: .08" in blink[:200]
    # The lift and the ring play TOGETHER: one animation list, or the later rule wins.
    both = rule(".card.coach.nudge")
    assert "coachblink" in both and "tilenudge" in both
    # Nothing replaced the old breathing ring by stealth.
    assert "coachring" not in css
    # Reduced motion: still ringed, standing still.
    guard = css[css.index("@media (prefers-reduced-motion: reduce) {\n    .card.nudge, .card.coach"):]
    guard = guard[:guard.index("}")]
    for sel in (".card.coach", ".card .pick.coach", ".vbig.coach::after", ".gridtip .st.now", ".vguide"):
        assert sel in guard, sel


def test_the_like_step_never_lights_the_heart_like_a_like():
    """Right after ≈ the reference photo leads the refined grid, and the like step lit
    ITS heart filled in violet — which is exactly what a liked heart looks like. Read as
    "≈ liked it for me". The heart keeps its dark plate and blinks, on the first tile
    that is neither the reference nor already liked."""
    js, css = widget._WIDGET_JS, widget._STYLE
    pick = css[css.index(".card .pick.coach {"):]
    pick = pick[:pick.index("}")]
    assert "background" not in pick
    target = js[js.index("function likeTarget()"):js.index("function paintCoach()")]
    assert "const ref = starredHere();" in target
    assert "c.dataset.pid !== ref && !kept.has(c.dataset.pid)" in target
    paint = js[js.index("function paintCoach()"):js.index("/* The first tile lifts")]
    assert "const card = likeTarget();" in paint


def test_the_deck_bubble_is_placed_without_a_transform():
    """It rose from over the NEXT button and jumped to its own when the entrance ended:
    it was centred by `translateX(-50%)` and entered with `rise`, whose keyframes
    animate `transform` — so for the whole entrance the centring was gone. Same lesson
    as the toast (`toastin`). Now `left` alone places it, measured off the button, the
    entrance has keyframes of its own, and the arrow follows `--ax` when the plate is
    pushed back inside the frame."""
    js, css = widget._WIDGET_JS, widget._STYLE
    g = css[css.index(".vguide { position: absolute;"):]
    g = g[:g.index("}")]
    assert "translateX" not in g and "rise" not in g
    # Folded, it folds evenly: never one word alone on the second line.
    assert "text-wrap: balance" in g
    assert "animation: guidein" in g and "@keyframes guidein" in css
    guidein = css[css.index("@keyframes guidein"):]
    guidein = guidein[:guidein.index("} }") + 3]
    assert "translateX" not in guidein
    assert "left: var(--ax, 50%)" in css
    place = js[js.index("function placeGuide()"):]
    place = place[:place.index("\n}\n")]
    assert "guideBtn.getBoundingClientRect()" in place
    assert "Math.max(8, Math.min(mid - w / 2, vw - 8 - w))" in place
    assert 'guide.style.setProperty("--ax"' in place
    # Placed again whenever the deck resizes: a phone going full screen changes the
    # frame's width under it.
    assert "plateRO = new ResizeObserver(() => { sizeCards(); placeGuide(); });" in js


def test_liking_says_what_comes_of_it_and_stays_until_closed():
    """Once a photo is liked, the line says the assistant can use it now — the next move,
    not a receipt — and it STAYS on the grid until the reader closes it with the cross.
    It used to go on a timer (1.8s, then 7s), which took the one sentence about what to
    do with a liked photo away while the reader was still deciding. A frame reloaded
    under it brings it back as it was, "Liked!" included. In the deck it is said over
    the heart for a few seconds, without a ring: the deck has no cross to close it."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    assert 'const COACH_DONE_LIKED = "Liked! Now ask the assistant to use it in your project";' in js
    assert "const COACH_DONE_OTHER =" in js      # the last step was not a heart
    # No timer ends it on the grid: only the cross does.
    assert "COACH_FINAL_GRID_MS" not in js and "coachEndTimer" not in js
    off = [line.strip() for line in js.splitlines() if "coach.off = true" in line]
    # The others are not timers either: a host that runs the introduction itself (the
    # try-it page), or the server once the caller has met the coach (serverMutesCoach),
    # mutes it before the grid is ever painted — and a restored view does not bring it
    # back (restoreUi).
    assert sorted(off) == sorted(["coach.off = true;",
                                  "if (coachMuted()) coach.off = true;",
                                  "if (coachMuted()) coach.off = true;"]), off
    assert ("function coachMuted() { return hostMutesCoach() || serverMutesCoach(); }"
            in js)
    close = js[js.index("function coachOff()"):js.index("const COACH_SAY = {")]
    assert "coach.off = true;" in close
    assert "() => coachOff()" in js
    # Finished is not off after a reload, and the wording comes back with it.
    restore = js[js.index('if (ui.coach && typeof ui.coach === "object")'):]
    restore = restore[:restore.index("\n  }")]
    assert "coach.off = !!ui.coach.off;" in restore and "!coachNext()" not in restore
    assert "coachLast = typeof ui.coach.last" in restore
    assert "last: coachLast" in js[js.index("function coachState()"):]
    # The deck's few seconds over the heart.
    did = js[js.index("function coachDid(what)"):js.index("function coachOff()")]
    assert 'if (what === "like" && deckOpen) {' in did and "COACH_FINAL_DECK_MS" in did
    deck = js[js.index("function paintDeckCoach(next)"):js.index("function placeGuide()")]
    final = deck[deck.index("if (deckOpen && coachDeckFinal) {"):deck.index("} else if")]
    assert "coachDoneSay()" in final and "coach\")" not in final


def test_the_selection_block_says_what_it_is_for():
    """Readers liked photographs, watched them gather under the grid, and still asked
    whether the assistant could see them. The block's title answers before the
    question is asked — the photos below can be used directly in the chat — for as long
    as there is a selection, singular or plural as it is."""
    js, css = widget._WIDGET_JS, widget._STYLE
    body = js[js.index("function paintSelBlock()"):js.index("/* ── The trail")]
    assert '\'<p class="seltitle">\' + ICON.heartSel' in body
    assert '+ esc(selTitle(n)) + "</span></p>"' in body
    # The title itself, singular and plural, by the assistant's name or without one.
    title = js[js.index("function selTitle(n) {"):js.index("function paintSelBlock()")]
    assert '"Liked photo — use it directly in the chat with {assistant}",' in title
    assert '"Liked photos — use them directly in the chat with {assistant}", { assistant: who })' in title
    assert '"Liked photo — use it directly in the chat with the assistant",' in title
    assert '"Liked photos — use them directly in the chat with the assistant");' in title
    # Above the count and the clear button, not instead of them.
    assert body.index('class="seltitle"') < body.index('class="selhead"')
    assert ".seltitle {" in css and "text-wrap: balance" in css[css.index(".seltitle span"):][:60]

def test_the_star_says_what_it_does():
    """A star means "favourite" to most people, and readers pressed it thinking they
    were keeping the photograph — then found it had fetched a different set instead.
    The heart beside it is what keeps; two controls that both look like approval, one
    of which is not, is a trap with an icon on it.

    A word, not another glyph: there is no drawing of "more photographs like this one"
    that everybody reads the same way. It hangs below the button rather than sitting in
    the row, or it pushes the one button it explains out of line with the other four."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    # The icon carries it: a photograph with more stacked behind it, not a star.
    assert "approx:" in js and '"M12 2.1l3 6.2' not in js       # the star path is gone
    assert "const star = (size) => STROKE(D.approx" in js
    # No caption at all: ≈ needs none, which is the reason for choosing it.
    for gone in ("vsuplabel", "teachSimilar", "supTaught"):
        assert gone not in js and gone not in HTML, gone
    # The button stands alone in the row — no wrapper, no caption beside it.
    assert '<button class="vbig sup" type="button" aria-label="More photos like this one">' in HTML
    # Filled, not tinted: the one verb whose shape is not read without help takes the
    # colour whole. Declared ONCE — two equal `.vbig.sup` selectors meant the later won
    # and the glyph came out blue on blue, invisible.
    css = widget.GRID_HTML
    assert "background: var(--act-sup); color: #fff;" in css
    # One rule sets colour and plate; the media-query sizes are a separate, smaller
    # concern and may repeat the selector.
    assert css.count("background: var(--act-sup); color: #fff;") == 1


def test_there_is_no_hand_over_button():
    """It asked the reader to perform an act the widget had already performed: every
    heart publishes the selection to the model as it is made, and the server holds it
    besides. What the button added was a step people did not take — they picked their
    photographs, scrolled down and talked to the assistant, which is the natural thing
    to do and was the thing that did not work. That is fixed underneath, so the button
    is a leftover, and a leftover that implies the selection does not count until it is
    pressed. The foot it lived on went with it."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    for gone in ("sendSelection", "canSendToChat", "uploadPhotos", "handedOver",
                 "isAChange", "handOver(", "Use in chat", "chatcta"):
        assert gone not in js, gone
    assert "chatcta" not in HTML
    for gone in ("paintFoot", "footEl", "ctaEl"):
        assert gone not in js, gone
    for gone in ('id="gridfoot"', 'id="footcta"', ".gridfoot", ".ico.choose", "--act-send"):
        assert gone not in HTML, gone


def test_the_star_does_not_choose_a_photograph():
    """It used to shortlist the photograph it was asked about, on the reasoning that
    asking for more like this one is a vote for it. It is not: readers ask for more like
    a photograph they are UNSURE about, and they came out of the deck with a selection
    they had not made and then had to undo. The shortlist is what the heart says, and
    only that."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    body = js[js.index("function superLike()"):js.index("// Pointer drag:")]
    assert "setKeptPhoto" not in body
    assert "markStar();" in body          # the star still marks the card it came from


def test_no_button_is_labelled_with_one_host_s_name():
    """The widget runs wherever an MCP Apps host renders it. A button reading "Send to
    ChatGPT" is wrong in Claude and wrong in the next one after that; the verb is
    "Choose" and the destination is "the chat".

    The one place an assistant is named is the line under the name, which says where
    a like goes (asked for, 2026-10-01): by the name of the host it runs in, from the
    server or from the host itself, and "the assistant" in a host it does not know."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    assert "hostName" not in js            # the whole "which host is this" branch is gone
    assert 'return "ChatGPT"' not in js
    assert js.count('"ChatGPT"') == 1 and '[/chatgpt|openai/, "ChatGPT"]' in js
    note = js[js.index("function paintNote() {"):]
    note = note[:note.index("\n}\n")]
    assert '"Photos you like are shared with the assistant"' in note
    # The button that carried the neutral label is gone; what is left must stay
    # neutral all the same.
    assert "Use in chat" not in js and "Sent to the chat" not in js


def test_every_glyph_is_filled():
    """A stroked icon reads as a control you might toggle and thins to nothing at 16 px
    over a photograph; a solid one reads as a verb. Solid is also what lets the button
    plate stay white while the glyph carries the colour that tells the four apart.

    Three exceptions, all of them lines by nature and none of them a verb:

      STROKE  — ≈, "more like this". Two waves FILLED at 28px close up into two blobs,
                and this is the one glyph that has to be read instantly.
      shapeGlyph — the format of a photograph, drawn as its own outline. A filled
                rectangle at 15px is a blob sitting on the picture; the outline is what
                carries the proportion, which is the entire content of the mark.
      everyShape — the same three outlines nested, for the filter button at rest.
      shapesGlyph — the shapes a filter holds, drawn together on the same grid, for
                the filter button when two are on: outlines nest, fills would merge
                into one blob that says neither.

    Counted rather than allow-listed by name: the point is that a fifth stroked glyph
    has to come here and justify itself."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    assert 'fill="currentColor"' in js
    assert js.count('fill="none"') == 4 and js.count("stroke-width") == 4
    assert "const STROKE = (d, size, w)" in js
    assert "D.approx" in js[js.index("const STROKE = (d, size, w)"):][:400]
    # Rewind is a closed loop with an arrowhead, not a chevron: it undoes, it does not
    # merely go back one.
    assert "rewind:" in js and js.count("M13 4.07V1L8.45 5.55") == 1


def test_the_verdict_marks_are_glyphs_on_the_edge_the_gesture_travels_to():
    """They were grey pills reading KEEP / SKIP / SIMILAR — three words to read at the
    one moment the reader's eye is on a photograph and their thumb is already moving.
    They are the buttons' own glyphs now, one per side, each on the edge its gesture
    goes towards, sitting on the picture rather than at the deck's edge."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    assert "ICON.keepBig" in js and "ICON.passBig" in js and "ICON.supBig" in js
    for word in ("<span>Keep</span>", "<span>Skip</span>", "<span>Similar</span>"):
        assert word not in js, word
    # ON the card, so the stamp tilts and travels with the photograph, and on the corner
    # the card is LEAVING: a heart pinned to the right edge of a card thrown right is off
    # screen before the throw is worth confirming, and one hovering at the deck's centre
    # sits over a picture that has moved out from under it. Both were tried.
    assert ".vstamp.keep { left: 5%;  top: 5%" in HTML
    assert ".vstamp.pass { right: 5%; top: 5%" in HTML
    assert ".vstamp.sup  { left: 50%; bottom: 24%" in HTML
    assert "rotate(-18deg)" in HTML and "rotate(18deg)" in HTML
    # A BADGE, not a billboard. At a third of the card these reached 270 px on a
    # chat-width deck: a red cross sprawled over the middle of the very photograph
    # the reader was trying to look at. Capped at the size of the round verb it echoes.
    css = HTML[HTML.index(".vstamp { position:"):HTML.index(".vstamp svg")]
    assert "width: 14%" in css and "max-width: 76px" in css
    assert "34%" not in css
    # The scale answers the thumb, with no transition to chase it.
    assert "function markTf(kind, r)" in js and '0.66 + r * 0.46' in js
    # A button press throws the same stamp the gesture would.
    assert "function flashMark(kind)" in js
    assert "@keyframes flashkeep" in HTML and "@keyframes flashpass" in HTML


def test_position_in_the_deck_is_shown_as_dashes():
    """"5 / 16" is a number to do arithmetic on. Sixteen dashes with the fifth lit is a
    position, read without being parsed."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    assert "function paintSegs()" in js
    assert ".vsegs" in HTML and ".vsegs i.now" in HTML and ".vsegs i.done" in HTML
    assert "vcount" not in HTML and ".vbar" not in HTML
    # Rebuilt only when the result count changes — sixteen nodes per swipe is a flicker.
    assert "if (segs.childElementCount !== n)" in js


def test_the_way_out_of_the_deck_is_a_download_button():
    """A worded "See on Pexafy" link sat on a line of its own under the verbs and cost
    the deck about forty pixels — which a portrait photograph wants far more than a
    second way of saying Pexafy. It is the sixth round button, in its own hue, and it
    opens the photo's page because the app sandbox cannot hand over a file itself."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    assert "vopen" not in HTML and "See on Pexafy" not in HTML
    assert "vbig get" in HTML and ".vbig.get    { color: var(--act-get); }" in HTML
    assert 'querySelector(".vbig.get").addEventListener' in js
    assert "f.pexafyUrl || footUrl" in js


def test_the_shortlist_is_not_a_scrolling_box():
    """A scrollbar under eight thumbnails made the shortlist look like a widget inside
    the widget, and hid half of it — on the one thing whose whole job is to show the
    reader what they picked. It is a grid that grows downwards; nothing is ever behind
    a scrollbar."""
    css = HTML[HTML.index(".selgrid { display: grid;"):HTML.index(".seltile { position:")]
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)      # the rules, not the notes
    assert "overflow" not in css and "max-height" not in css
    assert "repeat(auto-fill" in css


def test_the_picture_and_its_verbs_are_one_block():
    """They were pinned to opposite ends of the surface, so on a tall device a landscape
    photo sat marooned with three hundred pixels of dead ground under it. The deck is cut
    to the photograph's own box and the buttons ride directly beneath it, the pair
    centred in whatever height the host gives."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    assert ".vbody" in HTML and "justify-content: center" in HTML
    assert 'deck.style.height = deckH + "px"' in js
    # Measured off the body, never off the deck — the deck's height is the output.
    assert "body.clientHeight - (ctl" in js
    assert 'plateRO.observe(viewerEl.querySelector(".vbody"))' in js


def test_the_row_is_five_verbs_and_one_way_back():
    """Going back and getting the file are not decisions about the photograph, so they
    were moved out of the verb group and made plateless grey glyphs — and quiet turned
    out to mean invisible, for the two controls a reader looks for FIRST.

    What was missing is the PLATE. The words that came with it are not: seven controls
    under a photograph is already a full row, and two of them wearing labels made it a
    sentence. A shadow, a shape of their own, and the download's own hue."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    assert "vclose" not in HTML and "vbig exit" not in HTML
    assert ".vverbs" in HTML and ".vside" in HTML
    assert "grid-template-columns: 1fr auto 1fr" in HTML
    assert ".vside.back { justify-self: start; }" in HTML
    # Download came in off the edge to take the tick's place — one button fewer, and
    # the only thing left outside the group is the way back, which is not a verb.
    assert ".vside.get" not in HTML and "vside get" not in HTML
    assert HTML.count('class="vside') == 1              # only `back` is left outside
    # A plate, not a bare glyph: a background and a shadow to lift it off the sheet.
    side = HTML[HTML.index(".vside { display:"):HTML.index(".vside svg")]
    assert "background: var(--v-btn)" in side and "box-shadow:" in side
    # …and no words on it.
    wiring = js[js.index("/* ── Wiring"):]
    assert "lbl" not in wiring and ".vside .lbl" not in HTML
    assert 'querySelector(".vbig.get").innerHTML = ICON.get;' in js
    assert 'querySelector(".vside.back").addEventListener("click", closeViewer)' in js
    # A left-pointing arrow, not a door and not a cross.
    assert "back:  \"M20 11H7.8l5.6-5.6" in js


def test_a_tap_on_the_photograph_does_nothing():
    """The card is captured for the drag, so the browser reports a plain tap against
    `.vcard` — which spans the whole deck — and `closest(".shot")` therefore missed:
    tapping the middle of the picture closed the viewer. Where the tap landed is a
    question about geometry, and it is answered with the photograph's own rectangle."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    body = js[js.index('viewerEl.addEventListener("click"'):]
    assert "getBoundingClientRect()" in body[:900]
    assert "e.clientX >= r.left && e.clientX <= r.right" in body[:900]


def test_the_grid_opens_on_six_and_grows_by_six_then_four():
    """Sixteen results at once is a contact sheet: about 130px a tile on a chat surface,
    too small to answer the only question being asked of them.

    Six, six, four — the same three presses on every surface. The counts used to be
    chosen per surface on the assumption that a phone is two columns and a chat window
    three; the assumption is wrong. The column rule is one line of CSS reading the
    WIDTH, so a phone whose frame is 440px wide gets three columns like anything else —
    measured on a Redmi, which opened four photographs as a row of three and an orphan
    underneath. Six is the number that does not care: three rows of two, two rows of
    three, a full row either way.

    A page keeps no more photos than the grid can show, and the rest of the result set
    is one press away on Pexafy."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    assert "const GRID_PAGES = [6, 6, 4];" in js
    assert "function gridFirst() { return GRID_PAGES[0]; }" in js
    assert "const GRID_ROWS" not in js
    # No per-surface counts left anywhere: one grid, one set of steps.
    for gone in ("GRID_FIRST_PHONE", "GRID_MORE_PHONE", "GRID_MAX_PHONE",
                 "GRID_SHOWN_MAX", "const GRID_FIRST =", "const GRID_MORE ="):
        assert gone not in js, gone
    # What a page keeps is what the grid can show, from the one list of steps.
    assert "const GRID_MAX = GRID_PAGES.reduce((a, b) => a + b, 0);" in js
    assert "function gridShownMax() { return GRID_MAX; }" in js
    assert "raw.slice(0, GRID_MAX)" in js
    # The steps are read off where the reader has got to, because they differ in size.
    assert "for (const n of GRID_PAGES) { at += n; if (shown < at) return at - shown; }" in js
    # The cap hides cards; it does not shorten PHOTOS.
    body = js[js.index("function capShown()"):js.index("/* The one control under the grid")]
    assert "card.hidden = i >= shown" in body
    assert "PHOTOS" not in body
    # Every grid opens on its first page — walking back up the trail included.
    assert "shown = gridFirst();\n  paintGrid();" in js
    assert ".moreb" in HTML
    assert "shown += gridMore();" in js
    assert "Math.min(PHOTOS.length, gridShownMax()) - shown" in js
    assert "Math.min(gridMore(), left)" in js


def test_the_steps_add_up_to_the_grid_and_stop_there():
    """A press adds what is left of its step, and the last one leaves nothing: 6 → 12 →
    16, then the way out to Pexafy. Evaluated rather than trusted, because the button's
    label and the grid's cap read the same function, and a mismatch between them is a
    button that says one number and shows another."""
    pages = [6, 6, 4]

    def more(shown):
        at = 0
        for n in pages:
            at += n
            if shown < at:
                return at - shown
        return 0

    assert sum(pages) == 16
    assert (more(0), more(6), more(12), more(16)) == (6, 6, 4, 0)
    # Every step but the last ends on a full row at two columns AND at three.
    assert all(n % 2 == 0 and n % 3 == 0 for n in pages[:2])


def test_one_slot_under_the_grid_holds_one_control():
    """"See 6 more" and "Full results at Pexafy" were two buttons in two places: the
    first under the grid, the second alone at the very bottom of the widget, a white
    pill on a white sheet with a hairline border. A reader looking straight at the
    second one did not find it.

    One slot, directly under the grid, and it changes hands once — "See 6 more" while
    something is hidden, the way out to Pexafy from then on, which is the honest answer
    to "and the rest?". Filled, so it is seen."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    slot = js[js.index("function paintSlot()"):js.index("/* ── Who is asking")]
    assert '"See {n} more"' in slot
    assert 'class="brandcta"' in slot and "moreb" in slot
    assert 'openLink(footUrl, "cta")' in slot
    # It is the only place either of them is painted.
    assert js.count('class="brandcta"') == 1
    # Filled, not a hairline pill on white: with the brand colour, a solid one.
    cta = HTML[HTML.index(".brandcta { display:"):HTML.index(".brandcta:hover")]
    assert "background: var(--brand)" in cta and "border: 0" in cta
    assert re.findall(r"--brand:\s*([^;]+);", HTML) == ["#7c3aed"]


def test_a_photograph_is_the_same_size_wherever_it_is_met():
    """The deck took the grid's height, full stop — and a refined grid is SHORT, because
    "more like this one" returns seven photos where a search returns sixteen: two rows
    instead of four. The same photograph opened from the search grid filled 533x637 and
    from the refined grid 285x341, and under 470px the compact rule — written for a host
    that CLIPS the widget — hid its caption too. Half the picture and none of the words.

    Three things set the height and the largest wins: a floor clear of that breakpoint,
    the grid being covered, and the tallest deck already shown. Then a ceiling over all
    three — a three-column grid the reader has expanded twice is 1300px tall, and
    matching it strands the photograph in the middle of a field of white."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    assert "const DECK_MIN = 520;" in js and "let deckH = 0;" in js
    assert "const DECK_MAX = 800;" in js
    open_fn = js[js.index("function openViewer(i, list)"):js.index("/* ── Fitting a photograph")]
    assert "Math.min(DECK_MAX, Math.max(DECK_MIN, deckH," in open_fn
    assert "deckH = h;" in open_fn
    # The floor has to clear the compact breakpoint, or the caption goes with it.
    assert "@media (max-height: 470px)" in HTML
    assert ".vinfo .cap { display: none; }" in HTML


def test_three_columns_is_a_ceiling_not_a_wish():
    """A thumbnail is being asked "is this the photograph?", and at a quarter of a chat
    column it cannot answer. Three across is about 180px — a face, a horizon, a crop.

    `minmax(140px, 1fr)` on its own only ever set a FLOOR, which is how a wide surface
    used to fit five; the minimum is the larger of 140px — what stops a phone squeezing
    in tiles too small to judge — and an exact third of the row, gaps deducted."""
    css = HTML[HTML.index(".grid { display: grid;"):HTML.index(".card { position:")]
    assert "minmax(max(140px, (100% - 20px) / 3), 1fr)" in css
    assert "minmax(140px, 1fr)" not in css
    # 20px is the two 10px gaps between three tracks — they have to stay in step.
    assert "gap: 10px" in css


def test_speaking_brings_the_answer_into_view():
    """A turn spoken in the thread lands BELOW the widget, and the reader is looking at
    the widget. `scrollIntoView` from inside a cross-origin iframe scrolls the ancestor
    frames — the one lever the sandbox leaves for moving the host's page."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    assert "function followTheAnswer()" in js
    assert 'el.scrollIntoView({ block: block || "center", behavior: "smooth" })' in js
    assert 'function followTheAnswer() { reveal(document.getElementById("end"), "end"); }' in js
    # The 380ms delay WAS the bug: the host scrolls to the turn it has just been given,
    # further down, and then the timer fired and pulled the view back up to the foot of
    # the widget. Down, then back up, on every hand-over.
    assert "}, 380);" not in js
    assert 'id="end"' in HTML
    # Every path that speaks follows: the shortlist, one photo, and "more like this".
    assert js.count("followTheAnswer();") == 1   # the star's fallback, and only it


def test_the_deck_is_a_page_of_the_widget_not_a_modal():
    """It was an overlay pinned to the slice of the widget that happened to be on
    screen, found with an IntersectionObserver — and that slice changes as the reader
    scrolls. The deck was measured against it, so the photograph shrank on the way down
    the conversation, the widget shrank with it, and it stayed shrunk after closing.

    It takes the grid's place and the grid's HEIGHT instead, so the widget reports the
    same size open as closed and nothing in the thread moves either way."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    assert "IntersectionObserver" not in js and "watchBand" not in js
    assert 'id="band"' not in HTML
    assert "#viewer { position: relative;" in HTML
    # The height is read BEFORE the grid is hidden — a hidden element measures zero.
    open_fn = js[js.index("function openViewer(i, list)"):js.index("function closeViewer()")]
    assert open_fn.index("gridEl.offsetHeight") < open_fn.index("gridEl.hidden = true")
    assert 'viewerEl.style.height = ""' in js     # and given back on the way out


def test_what_is_handed_to_the_chat_is_publishable():
    """A caption and an id told the model what it already knew and gave the reader
    nothing they could print. What goes up now is where the photo lives, the credit
    they are obliged to carry, its two halves separately, and then the handle."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    assert "function photoLines(f, indent)" in js
    body = js[js.index("function photoLines(f, indent)"):js.index("function selectionText()")]
    # Order matters: the URL leads.
    assert body.index("f.pexafyUrl") < body.index("Attribution: ")
    assert body.index("Attribution: ") < body.index("Photographer: ")
    assert body.index("Photographer: ") < body.index("Source: ")
    assert body.index("Source: ") < body.index("photo_id")
    # Every path that hands a photo over uses it: the definition, the two turns, and
    # the file hand-over's own note.
    assert js.count("photoLines(f") == 2      # the definition, and the one hand-over


def test_asking_for_similar_photos_leaves_the_deck():
    """Either way the answer is somewhere the deck is covering — in the grid where the
    widget ran the search itself, in the thread where it had to ask. The star marks the
    card and gets out of the way, the same reason choosing a photo closes."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    assert "if (askSimilar()) setTimeout(closeViewer" in js
    body = js[js.index("function superLike()"):js.index("// Pointer drag:")]
    # Refine FIRST, close after — and the order is load-bearing. Closing hands the
    # screen back, a mode change a host may answer by re-parenting (and so reloading)
    # the iframe; closing first asked for the refinement from a frame that no longer
    # existed. Measured on a phone in grid_lab: the star took the reader back to the
    # search they started from.
    assert "refineFrom(f).then(ok => { if (ok && deckOpen) closeViewer(); });" in body
    assert "closeViewer();\n      refineFrom(f);" not in body


def test_the_star_refines_the_grid_it_is_standing_in():
    """It used to put a sentence in the conversation and wait for the assistant to run
    the tool again: a second widget, a second grid, and the first one — with whatever
    the reader had shortlisted in it — scrolled off the top of the screen.

    Where the host proxies a tool call the widget runs the search itself and repaints
    the grid already on screen. Where it does not, the sentence in the thread is still
    there; neither path is dropped."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    assert "async function refineFrom(f)" in js
    # The by-image tool, with a catalogue photo as its reference: there is no similar
    # tool, `photo_id` is one of the four ways that tool takes one.
    assert f'callServerTool("{tooling.PUBLIC_TOOL_NAMES["search_photos_by_image"]}", args)' in js
    # With the words the photo was found under: a refinement without them drifted off
    # its subject at every step — the neighbours of a red bicycle against a wall are,
    # two steps on, walls. They ride on the page's origin, so a refinement made from a
    # refinement still carries the search's own `q`.
    refine = js[js.index("async function refineFrom(f)"):js.index("/* ── Shape:")]
    assert "const words = wordsOf(trail[step]);" in refine and "if (words) args.english_search_sentence = words;" in refine
    assert "function wordsOf(page)" in js
    # …and the sentence spoken where no host proxies a tool repeats them too.
    ask = js[js.index("function askSimilar()"):js.index("/* ── Rendering a tool result")]
    assert "from my search" in ask
    # Both surfaces, because neither is universal: the protocol capability first, then
    # ChatGPT's own. A host with neither is TOLD so — the star cannot look like it
    # simply did nothing.
    tool = js[js.index("async function callServerTool(name, args)"):js.index("function refineExhausted()")]
    assert 'hostCan("serverTools")' in tool and "oai.callTool(name, args)" in tool
    assert "throw new Error(" in tool
    # And the old road is still the road where the host cannot proxy anything.
    assert "function askSimilar()" in js and "canProxyTools()" in js


def test_a_refined_grid_opens_on_the_photo_that_was_chosen():
    """The reader starred a photograph; a grid of strangers that merely resemble it
    loses the thing they were holding on to. It leads the new grid, wearing its star,
    at #1 — and it is COPIED in, because the same object is still sitting in the grid
    it came from and renumbering it there would change a rank under a tile nobody
    touched."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    body = js[js.index("function buildPhotos(sc, lead)"):js.index("function paintTrail()")]
    assert "Object.assign({}, lead)" in body
    assert "[head].concat(out.filter(f => f.id !== head.id))" in body
    # And it is not renumbered anywhere: nothing in the grid carries a number at all.
    assert "f.num" not in body and "rank" not in body
    # The star rides the photo into the grid, not just the deck.
    assert ".card .cstar" in HTML
    assert "const starred = starredHere();" in js[js.index("function paintGrid()"):]


def test_one_star_per_page_at_most_and_going_back_takes_it_away():
    """The star was remembered in a Set, and a Set only ever grows: walking back up the
    trail and choosing a different photograph left the abandoned one wearing a star it
    had no right to, and the first page ended up with four of them.

    The trail IS the record. On any page the star marks the photo that took the reader
    forward from it; on the page they have not moved on from, it marks the one that
    brought them there — the photo leading the grid. At most one, by construction, and
    truncating the trail takes the abandoned star with it."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    assert "starredIds" not in js                  # the Set is gone, not merely unused
    body = js[js.index("function starredHere()"):js.index("/* The photos of one result set")]
    assert "trail[step + 1]" in body and "trail[step]" in body
    # Both surfaces read the one function — no second, divergent rule.
    assert "f.id === starredHere()" in js and "f.id === starred" in js


def test_the_refinement_ceiling_is_a_depth_not_a_trail_length():
    """Measured on the trail's length, reaching the sixth refinement killed the star on
    every page at once — including the ones the reader had walked back to, where going
    on is legitimate because it throws the steps ahead away. It is the depth of the page
    you are standing on: at six there is nowhere further to go, below six there is, and
    the trail still cannot grow past six either way."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    assert "function refineExhausted() { return step >= MAX_REFINE; }" in js
    assert "const MAX_REFINE = 6;" in js


def test_the_selection_says_what_it_holds_and_how_it_is_ranked():
    """A count and a round glyph are a legend nobody was given. The block names what it
    is holding — and names the ranking, because the order the reader kept them in IS
    what goes to the chat as #1..#n, and nothing on screen said so."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    body = js[js.index("function paintSelBlock()"):js.index("/* ── The trail")]
    assert '"{n} image selected"' in body and '"{n} images selected"' in body
    assert ".selhead" in HTML
    # The ranking is SHOWN, on the corner of each photo, not spelled out in the header:
    # a caption explaining a number is a number that should have been on the picture.
    assert "ranked #1 to" not in js
    assert '<span class="rk grip"' in body and "ICON.grip + '#' + (i + 1)" in body
    assert ".seltile .rk {" in HTML
    # The verb moved to the foot; what is left here is the count and the way to drop one.
    assert "Use in chat" not in body
    assert "data-drop=" in body


def test_the_trail_is_the_way_back_to_every_search():
    """A refinement that cannot be undone is one the reader will not risk making. Every
    step keeps its own results, so going back is a repaint rather than a second round
    trip, and the anchor is the search the assistant ran — the one step they did not
    choose and must never lose."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    assert 'id="trail"' in HTML and ".tstep" in HTML and ".tsep" in HTML
    assert "anchor:" in js and ".tstep.anchor" in HTML
    assert "function paintTrail()" in js and "function goToStep(n)" in js
    # The step carries its photos: showStep() reads them, it does not fetch them.
    show = js[js.index("function showStep()"):js.index("/* Going back is not undoing")]
    assert "PHOTOS = s.photos;" in show and "callServerTool" not in show
    # A trail of one is a decoration, not a way back.
    assert "const on = trail.length > 1;" in js


def test_refining_from_a_step_drops_the_branch_not_taken():
    """Walking back up the trail leaves it intact — the reader may be checking one step
    against another. Refining from where they stand is the moment the steps ahead
    become a branch they did not take, and only then are they dropped."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    back = js[js.index("function goToStep(n)"):js.index("async function refineFrom(f)")]
    assert "trail.length = " not in back         # going back truncates nothing
    assert "step = n;" in back and "showStep();" in back
    assert "trail.length = step + 1;" in js      # refining does


def test_six_refinements_is_the_ceiling():
    """A trail that never ends is a trail nobody can read, and every step is a billable
    search. Past the sixth the star is a dead button rather than one that blooms and
    then apologises."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    assert "const MAX_REFINE = 6;" in js
    assert "function refineExhausted()" in js
    assert 'querySelector(".vbig.sup").disabled = canProxyTools() && refineExhausted()' in js
    # Checked before the celebration, so the gesture is refused rather than half-played.
    body = js[js.index("function superLike()"):js.index("// Pointer drag:")]
    assert body.index("refineExhausted()") < body.index("burstStar();")


def test_the_shortlist_survives_the_grid_it_was_built_in():
    """It was a set of INDEXES. Refining replaces the photos under those positions, so
    a reader who kept three photographs and then asked for more like one of them would
    have watched their shortlist turn into three different pictures. It is keyed by
    photo_id now, and it holds the photo itself — one kept three searches ago is still
    there to be sent when its result set is long gone."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    assert "const kept = new Set();          // photo_id" in js
    assert "const keptPhotos = new Map();" in js
    assert "function keptList() { return Array.from(keptPhotos.values()); }" in js
    assert "function isKept(i)" in js and "function cardOf(id)" in js
    # The tile is found by id, not by position — the same photo sits at a different
    # rank in every step of the trail.
    body = js[js.index("function setKeptPhoto(f, on)"):js.index("function setKept(i, on)")]
    assert "cardOf(f.id)" in body


def test_the_model_is_told_where_the_grid_is_without_a_turn_in_the_thread():
    """A refinement happens inside the widget, so the conversation sees nothing of it —
    and "use the second one" is then about a grid the assistant has never been shown.
    It is pushed as context, not spoken, and it carries BOTH facts in one call: a host
    keeps the last context the view pushed, so two functions each pushing half of it
    would take turns erasing the other."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    body = js[js.index("function writeContextToModel()"):js.index("function setKeptPhoto(f, on)")]
    assert "refined_from_photo_id" in body and "selected_photo_ids" in body
    assert "grid_photo_ids" in body
    assert "tiles carry no numbers" in body
    assert "sendMessage" not in body and "sendFollowUpMessage" not in body
    # One pusher, called from both the shortlist and the trail.
    assert js.count("app.updateModelContext({") == 1


def test_the_grid_stays_on_screen_while_a_refinement_is_in_flight():
    """Emptying the widget and filling it back up loses the reader their place for the
    length of a round trip. The grid it is replacing stays, dimmed, with a bar sweeping
    under the head."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    assert ".grid.busy" in HTML and ".gridhead.busy::after" in HTML
    assert "@keyframes sweep" in HTML
    body = js[js.index("async function refineFrom(f)"):js.index("/* ── The deck ")]
    assert 'gridEl.classList.add("busy")' in body
    # And it always comes back, whichever way the call ends.
    assert "} finally {" in body and 'gridEl.classList.remove("busy")' in body


def test_the_photo_pages_belong_to_the_server_that_answered():
    """Hard-coding pexafy.com sent every preprod reader to production — and made ChatGPT
    ask permission for one link and not the other, because a host opens a link inside
    the app's own declared domain without asking and the footer was a different host
    from the photo pages. The server names its site in `cta.url`."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    assert "function adoptSite(url)" in js
    assert "let PEXAFY_PHOTO_BASE" in js
    # Adopted before the photos are built — fields() writes each page URL from it.
    # Every result set goes through buildPhotos, the anchor and every refinement
    # alike, so the site is read there rather than on the one path render() owns.
    body = js[js.index("function buildPhotos(sc, lead)"):js.index("function paintTrail()")]
    assert body.index("adoptSite(cta.url)") < body.index("fields(photo")


def test_the_grid_is_signed_top_left_and_the_deck_is_not_signed_at_all():
    """The wordmark spent a while at the top of the DECK — branding laid over a
    photograph the reader was in the middle of judging, in the one place the widget
    should be showing a picture and nothing else. It signs the grid instead: top left,
    at the size it has in the footer's call to action, so the results page is signed at
    both ends and the deck is clean."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    assert "vbrand" not in HTML and "vbrand" not in js
    assert '<button class="brandmark" type="button">Pexafy</button>' in HTML
    # The name, not the logo (test_no_logo_and_no_custom_gradient).
    assert "background-clip" not in HTML
    # It leads the head. At the footer button's 15px it read as a caption beside a
    # row of 26px thumbnails, which is not what a signature is for.
    head = HTML[HTML.index(".brandmark {"):HTML.index(".brandmark:hover")]
    assert "font-size: 20px" in head and "font-weight: 700" in head
    assert HTML.index('class="brandmark"') < HTML.index('id="trail"')
    # It makes the footer's promise, so it carries the footer's words and its link.
    assert 'brandEl.addEventListener("click", () => openLink(footUrl, "wordmark"))' in js
    assert "brandEl.title = tx(footLabel)" in js


def test_the_last_swipe_gives_the_grid_back():
    """The deck used to end on a summary screen: the shortlist again, a sentence, and
    four links. It was a second home for the selection, shown to a reader who had just
    finished choosing — and the only way to reach the crosses on it was to swipe all the
    way to the end of the results.

    The last card closes the deck. What was kept is under the grid, in the selection
    block, where it can be pruned and swiped again at any time."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    assert "paintSummary" not in js
    assert ".vsum" not in HTML and ".vcta" not in HTML and ".vlink" not in HTML
    body = js[js.index("function paintDeck()"):js.index("// Any real gesture means")]
    assert "if (idx >= total) {" in body and "closeViewer();" in body
    # …and the selection block is repainted on the way out, because it may have emptied
    # while the deck was over it.
    close = js[js.index("function closeViewer()"):js.index("/* The markup of one photo card")]
    assert "paintSelBlock();" in close and "paintSlot();" in close


def test_the_selection_is_a_deck_of_its_own():
    """"Swipe again through what I kept" is a different set from "swipe through the
    results", and the deck used to be able to walk only one of them: the grid. It walks
    a LIST now — the grid by default, the shortlist when the selection block opens it —
    so the second pass happens on the six photos that survived the first.

    In that pass a left swipe DROPS: skipping a photo you are already holding means
    letting it go, which is the same verb as the red cross on its tile. In the grid's
    own deck a skip decides nothing, because the photo was never kept."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    assert "let DECK = [];" in js and "let reviewing = false;" in js
    assert "DECK = reviewing ? list : PHOTOS.slice(0, shown);" in js
    # What a skip MEANS no longer depends on the mode — see
    # test_a_skip_drops_a_photograph_the_reader_is_holding.
    assert "else if (isKept(idx)) setKept(idx, false);" in js
    # Nothing in the deck reads the grid any more — that was the bug this replaces.
    deck = js[js.index("function paintDeck()"):js.index("function rewind()")]
    assert "PHOTOS" not in deck
    # Walking back restores what the step changed, in BOTH directions: a photo dropped
    # in a review pass comes back when the reader takes the skip back.
    assert "if (h && h.was !== isKept(idx)) setKept(idx, h.was);" in js


def test_the_only_ranking_is_the_one_the_reader_made():
    """Every tile used to wear a `#` rank. A rank says the grid is ordered best-first —
    a claim about the search, printed sixteen times on a set the reader is about to
    reorder themselves by choosing among it. And a number nobody refers to by number:
    they point at a picture.

    The only ranking anyone acts on is the one that LEAVES — #1..#n over the photographs
    they kept, in the order they kept them, written into the turn that goes to the chat
    and used for the rest of the conversation ("use #2 for the header")."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    assert "let numbered" not in js and "f.num" not in js
    assert ".card .num" not in HTML and ".card .rank" not in HTML
    # The rank is the selection's own position, and it exists only where it leaves.
    sel = js[js.index("function selectionText()"):js.index("function pushContextToModel()")]
    assert 'list.map((f, i) => "#" + (i + 1)' in sel
    assert js.count('"#" + (i + 1)') == 1
    # Neither the deck's caption nor a single photo handed over counts anything.
    meta = js[js.index("function metaHtml(f)"):js.index("function paintSegs()")]
    assert "#" not in meta
    # And the model is told that a tile has no number, as a fact — the context carries
    # state, not orders ("never refer to one by position" was an instruction hidden from
    # the person, which Anthropic's directory refuses).
    ctx = js[js.index("function writeContextToModel()"):js.index("function setKeptPhoto(f, on)")]
    assert "photos are identified by photo_id" in ctx and "tiles carry no numbers" in ctx
    assert "never refer to one by position" not in ctx
    # The rank rides with the selection wherever it goes — now the tool's rows, and
    # the fallback list the context still writes when there is no tool.
    assert '"#" + (i + 1)' in js
    assert "rank: i + 1," in js


def test_the_selection_is_published_once_per_burst_and_numbered():
    """Measured against ChatGPT: a reader kept TWO photographs and was answered about
    one. Nothing here truncates, so what the model read was an intermediate state:
    every heart used to publish a context of its own, so keeping two published "one
    kept", then "two", and a host that serves a context it read a moment ago
    (openai/openai-apps-sdk-examples#221) answers with whichever it caught. Putting a
    reloaded frame's shortlist back was worse: one push per photograph.

    So the pushes are coalesced — the intermediate states stop existing rather than
    being hoped past — batches are applied with publishing held, and every push carries
    a revision, so two states can be told apart by something other than trust."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    assert "function pushContextToModel()" in js
    assert "function withHeldContext(fn)" in js
    assert "if (ctxHold) return;" in js
    assert "ctxTimer = setTimeout(() => { ctxTimer = null; writeContextToModel(); }, 90);" in js
    assert "ctxRevision++;" in js
    # The two batches that used to publish a state per item.
    for batched in ("for (const id of ui.kept) {", "for (const f of keptList()) if (f.id !== exceptId)"):
        at = js.index(batched)
        assert "withHeldContext" in js[at - 400:at], batched
    # Nothing publishes at once: every change is a burst of likes, which is what the
    # coalescing is for.
    push = js[js.index("function pushContextToModel()"):]
    push = push[:push.index("\n}\n")]
    assert "writeContextToModel();" in push and push.count("writeContextToModel") == 1


def test_every_link_says_which_surface_it_came_from():
    """A click arriving on pexafy.com from inside someone else's iframe is
    indistinguishable from any other referral. The grid is not only ChatGPT's — Claude
    renders it today, and a Copilot, a WordPress plugin or a Figma panel will next — so
    every link it opens carries who sent it and which control was pressed.

    `utm_*`, because the traffic pipeline already parses those: a new surface shows up
    in the numbers the day it appears, with no server change. And ONLY on our own
    origin: a preview URL is HMAC-signed and a provider's page is not ours to tag."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    body = js[js.index("function tagged(url, what)"):js.index("function toast(msg, show)")]
    assert 'if (u.origin !== PEXAFY_HOME) return url;' in body
    assert 'u.searchParams.set("utm_medium", "mcp_grid");' in body
    assert 'u.searchParams.set("utm_source", surface());' in body
    # One door out, so nothing can leave untagged by accident.
    assert 'app.openLink({ url: tagged(url, what) })' in js
    assert js.count("app.openLink(") == 1
    # Which host this is: it names itself in the handshake; ChatGPT is recognisable
    # even when it does not.
    surf = js[js.index("function surface()"):js.index("/* Only ever our own pages.")]
    assert "app.getHostVersion" in surf and "c.userAgent" in surf and '"chatgpt"' in surf
    # Every control that leaves the widget says which one it was.
    # `"photo"` went with the file hand-over: it named the uploaded file, never a link.
    for what in ('"cta"', '"wordmark"', '"download"', '"similar"', '"chat"'):
        assert "openLink(" in js and what in js, what
    # The page URL handed to the conversation is a click waiting to happen, so it is
    # tagged too — but never the signed CDN URL beside it.
    lines = js[js.index("function photoLines(f, indent)"):js.index("function selectionText")]
    assert 'tagged(f.pexafyUrl, "chat")' in lines
    assert "tagged(f.large" not in lines


def test_the_deck_says_what_is_already_kept():
    """A photo ticked in the grid, or any photo in a review pass over the selection,
    opened looking exactly like one that had never been chosen — and the heart read as
    "keep" when what it would actually do is drop it."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    assert 'keepBtn.classList.toggle("on", held);' in js
    assert ".vbig.keep.on {" in HTML


def test_the_position_dashes_go_where_they_point():
    """Sixteen dashes said where the reader was and nothing else: the eleventh photo was
    six swipes away, each of them a decision about a photograph they had not asked to
    judge. Every dash is a button now. The bar is 3px, so the button around it is 15px —
    a 3px target is not a target — and the click is delegated from the strip, so
    rebuilding the dashes never has to rewire anything.

    Jumping is not swiping: nothing is decided about the photo being left, and the undo
    stack is dropped, because a rewind from there would walk back a step never taken."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    assert "function goToPhoto(n)" in js
    body = js[js.index("function goToPhoto(n)"):js.index("function paintMeta(f)")]
    assert "undoStack.length = 0;" in body
    assert 'data-seg="' in js and 'closest("[data-seg]")' in js
    assert ".vsegs button" in HTML and "padding: 6px 0" in HTML
    # The hover rule outranks `.vsegs i.now` on specificity, so it has to exclude it:
    # a mouse resting on the current dash was wiping its colour back to grey.
    assert ".vsegs button:hover i:not(.now)" in HTML


def test_the_wordmark_keeps_the_site_s_weight_and_tracking():
    """The wordmark is the site's own weight and tracking (`.site-logo`, 700 and
    -0.03em in django_app/static/css/layout.css), set in the platform's typeface."""
    head = HTML[HTML.index(".brandmark {"):HTML.index(".brandmark:hover")]
    assert "font-weight: 700" in head and "letter-spacing: -.03em" in head
    assert "font-family: var(--font)" in head


def _luminance(hex_colour: str) -> float:
    """WCAG relative luminance of a #rrggbb colour."""
    def channel(c: int) -> float:
        s = c / 255
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4
    r, g, b = (int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def _contrast(a: str, b: str) -> float:
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _rule(selector: str) -> str:
    """The declarations of the first rule that starts with `selector`."""
    css = HTML[HTML.index(selector):]
    return css[:css.index("}")]


def test_no_logo_and_no_custom_gradient():
    """OpenAI's UI guidelines: "Do not include your logo as part of the response.
    ChatGPT will always append your logo and app name before the widget is rendered."
    and "Avoid custom gradients or patterns that break ChatGPT's minimal look.", with
    "Use brand accent colors on primary buttons inside app display modes."

    The name at the top left was the site's logo — 'Pexafy' clipped to its violet-to-cyan
    gradient — and the buttons and the deck's current dash were filled with the same
    gradient. The name is plain text in the ink colour; the buttons are one solid brand
    colour, white on it above 4.5:1 in both themes; the dash is the deck's ink."""
    mark = HTML[HTML.index(".brandmark {"):HTML.index(".brandmark:active")]
    assert "color: var(--fg)" in mark and "background: none" in mark
    for logo in ("gradient", "background-clip", "text-fill-color", "var(--brand)",
                 "transparent"):
        assert logo not in mark, logo
    # One value, both themes: the dark palette does not lighten it.
    assert re.findall(r"--brand:\s*([^;]+);", HTML) == ["#7c3aed"]
    assert "--brand" not in widget._DARK_TOKENS
    assert _contrast("#ffffff", "#7c3aed") >= 4.5
    assert "color: var(--on-primary); background: var(--brand)" in _rule(".gobtn {")
    assert "--on-primary: #ffffff" in HTML
    assert "color: #fff" in _rule(".brandcta {")
    for rule in (".gobtn {", ".brandcta {", ".vsegs i.now {", ".gridhead.busy::after {"):
        assert "gradient" not in _rule(rule), rule
    assert "background: var(--v-ink)" in _rule(".vsegs i.now {")
    # The gradient's cyan and light violet are gone with it.
    for hue in ("#22d3ee", "#06b6d4", "#a78bfa"):
        assert hue not in HTML, hue


def test_the_swipe_hint_plays_when_the_reader_arrives():
    """Nothing on screen says a photograph can be thrown, so two chevrons breathe
    outwards on arrival. It used to play once per WIDGET — one card, ever — so anyone
    who had opened a photograph earlier in the conversation never saw it again, and the
    deck read as a gallery. It plays once per OPENING: `coached` is reset by openViewer.

    And it is legible. A 34px disc at 42% black travelling 7px over a photograph is a
    smudge; it is 40px at 58%, ringed, travelling twice as far."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    open_fn = js[js.index("function openViewer(i, list)"):js.index("/* ── Fitting a photograph")]
    assert "coached = false;" in open_fn
    assert 'card.classList.add("sway");' in js
    assert "@keyframes sway" in HTML and "translateX(-19px) rotate(-2.6deg)" in HTML
    # Damped: each swing smaller than the last, ending where it started.
    assert "100% { transform: none; }" in HTML


def test_a_page_of_the_trail_turns_like_a_page():
    """The trail chips swapped one grid for another with no movement at all, so nothing
    said the reader had gone anywhere — which is why first readers did not read the
    chips as a way back. A page leaves in the direction it is going and the next arrives
    from the other side, with a degree and a half of tilt.

    And the thumb can turn it: a drag across the grid moves between the pages. It does
    nothing until the trail has a second page; it only takes over once the movement is
    unmistakably horizontal, so scrolling the conversation through the widget still
    scrolls the conversation; and at either end the grid rubber-bands rather than
    ignoring the hand."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    assert "function goToStep(n)" in js and "function snapGrid()" in js
    turn = js[js.index("function goToStep(n)"):js.index("async function refineFrom(f)")]
    assert "const dir = n > step ? 1 : -1;" in turn
    assert "if (REDUCED)" in turn                      # …and no movement at all there
    assert "enterPage(dir);" in turn
    assert "requestAnimationFrame(() => requestAnimationFrame(" in js
    # The gesture.
    gest = js[js.index("/* ── Turning the page with the thumb"):js.index("/* ── Wiring")]
    assert "trail.length < 2" in gest                  # a plain result grid is untouched
    assert "Math.abs(dx) < Math.abs(dy) * 1.4" in gest  # the conversation keeps the y axis
    assert "dx * 0.42 : dx * 0.11" in gest             # …and the ends rubber-band
    assert "if (gMoved > 8) { e.stopPropagation();" in gest
    assert "touch-action: pan-y" in HTML
    # A page slides out past the edge, and must not put a scrollbar on the widget.
    # `clip`, never `hidden` — hidden forces the other axis to compute as `auto`.
    body_css = HTML[HTML.index("body { position: relative;"):HTML.index("#status {")]
    assert "overflow-x: clip" in body_css and "hidden" not in body_css


def test_a_thumbnail_does_not_start_a_native_image_drag():
    """A thumbnail is an <img>, and an <img> is natively draggable: a drag begun on one
    fired `pointercancel` two frames in, so the page turn died with a 0.3px offset and
    the grid never moved. Both the property and the attribute, because Firefox reads the
    attribute and not the CSS."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    assert "-webkit-user-drag: none" in HTML
    assert '<img loading="lazy" alt="" draggable="false"' in js
    assert '<img src=\'' not in js
    sel = js[js.index("function paintSelBlock()"):js.index("/* ── The trail")]
    assert 'draggable="false"' in sel


def test_opening_a_photograph_brings_it_to_the_reader():
    """The grid can be twice the height of a screen, the tile they pressed is wherever
    they had scrolled to, and the deck replaces the WHOLE grid from its top — and is
    shorter than what it replaces. So opening the fifteenth photograph put the picture
    above the top of the screen and left the reader scrolling up to find the thing they
    had just asked for. `scrollIntoView` from inside a cross-origin iframe scrolls the
    ancestor frames: it is the one lever the sandbox leaves, and it is used."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    assert "function reveal(el, block)" in js
    open_fn = js[js.index("function openViewer(i, list)"):js.index("/* ── Fitting a photograph")]
    assert 'reveal(viewerEl, "center");' in open_fn
    # …after the deck is painted, not before: a hidden element has nowhere to scroll to.
    assert open_fn.index("paintDeck();") < open_fn.index("reveal(viewerEl")


def test_the_deck_walks_what_the_grid_is_showing():
    """A reader looking at nine tiles was handed sixteen dashes and a deck that went on
    long after the pictures they had been shown ran out — the deck was the result set,
    not the grid. It walks what is on screen; "See 6 more" grows both at once."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    assert "DECK = reviewing ? list : PHOTOS.slice(0, shown);" in js
    # And the model is told the same thing: what is on screen, not what came back.
    ctx = js[js.index("function writeContextToModel()"):js.index("function setKeptPhoto(f, on)")]
    assert "grid_photo_ids: PHOTOS.slice(0, shown).map(f => f.id)," in ctx
    assert "grid_photo_ids: PHOTOS.slice(0, shown)" in ctx


def test_leaving_the_deck_says_where_the_photographs_went():
    """Swiping is only worth doing if something comes of it, and what comes of it is a
    block at the foot of a widget the reader has never scrolled to. Closing a deck that
    changed the shortlist says where those photographs went — and the FIRST time, takes
    them there, because a sentence about a place is worth less than the place. After
    that it is a receipt, and a receipt does not move anybody."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    assert "let keptOnOpen = 0;" in js and "let selTaught = false;" in js
    assert "keptOnOpen = kept.size;" in js
    close = js[js.index("function closeViewer()"):js.index("/* The markup of one photo card")]
    assert "if (n && n !== keptOnOpen) {" in close
    assert "in the strip below the grid" in close
    assert "!selTaught" in close and "selTaught = true;" in close
    # A toast at the foot of a 1300px widget is a message to nobody unless it is shown.
    t = js[js.index("function toast(msg, show)"):js.index("function openLink(url, what)")]
    assert 'if (show) reveal(toastEl, "end");' in t


def test_a_refinement_arrives_the_way_a_page_arrives():
    """Nobody discovers a swipe by being told about it. The refined grid slides in from
    the right with the page turn's own tilt, at the one moment the reader is certainly
    looking at the grid — they pressed the star and are waiting for it. The movement is
    the instruction."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    assert "function enterPage(dir)" in js
    body = js[js.index("async function refineFrom(f)"):js.index("/* ── The deck ")]
    assert "enterPage(1);" in body
    assert body.index("showStep();") < body.index("enterPage(1);")
    # The same function the trail's own page turn uses — one movement, not two.
    turn = js[js.index("function goToStep(n)"):js.index("async function refineFrom(f)")]
    assert "enterPage(dir);" in turn


# ── The shape filter ────────────────────────────────────────────────────────
# Three things, and each one is a decision that was made rather than a detail that
# happened: what the tile says, what the button does, and what it does NOT do.

def test_every_tile_says_what_shape_it_is():
    """A reader comparing sixteen thumbnails cannot tell a 4:5 from a 3:4 by eye at
    140px, and the format is often the whole reason a photograph is unusable — a banner
    slot does not take a portrait.

    Drawn like the credit and not like the source chip: always on screen, white ink over
    the veil's own gradient, no plate. The source chip appears on hover, and a control
    that appears only to a mouse does not exist on a phone."""
    js, css = widget._WIDGET_JS, widget._STYLE
    assert "SHAPE_BOX[f.orientation]" in js          # only when the payload says so
    assert 'class="shape"' in js
    assert ".card .shape {" in css
    assert ".card:hover .shape" in css               # brighter under a pointer, not born there
    # Not a hover-only mark: the base rule has to carry an opacity of its own.
    base = css[css.index(".card .shape {"):]
    base = base[:base.index("}")]
    assert "opacity:" in base


def test_the_shape_mark_is_a_rectangle_in_proportion():
    """It says "portrait" to a reader who has never met the word, in every language the
    widget is opened in. An abbreviation would need translating; a word would not fit."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    assert "const SHAPE_BOX = {" in js
    for shape in ("landscape", "portrait", "square"):
        assert shape + ":" in js
    # The proportions are the content of the glyph: wider than tall, taller than wide,
    # and equal. Read off the source so a careless edit cannot invert one.
    box = js[js.index("const SHAPE_BOX = {"):]
    box = box[:box.index("};")]
    import re
    got = {}
    for name, nums in re.findall(r"(\w+):\s*\[([^\]]+)\]", box):
        _, _, w, h = [float(n) for n in nums.split(",")]
        got[name] = (w, h)
    assert got["landscape"][0] > got["landscape"][1]
    assert got["portrait"][1] > got["portrait"][0]
    assert got["square"][0] == got["square"][1]


def test_choosing_a_shape_re_asks_the_question_instead_of_hiding_tiles():
    """The difference is the whole feature. A page of sixteen results holds four
    portraits on a good day, and four tiles is not an answer to "show me vertical ones"
    — it is the same answer with most of it taken away. Pressing a shape re-runs the
    call that produced this grid with the format attached, and sixteen portraits come
    back."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    assert "async function applyShapes(list)" in js
    assert "callServerTool(here.origin.tool, args)" in js
    assert "args[ORIENT_ARG] = want;" in js
    # The public name, the one the tools advertise — the API's own `orientation` would
    # be dropped in silence by the tool layer and the filter would do nothing. Injected
    # from the registry, never copied into the JavaScript.
    assert f'const ORIENT_ARG = "{tooling.PUBLIC_ORIENTATION_PARAM}";' in js
    assert 'const ORIENT_ARG = "__ARG_ORIENTATION__";' in widget._WIDGET_JS
    assert tooling.PUBLIC_ORIENTATION_PARAM not in widget._WIDGET_JS


def test_what_the_frame_calls_and_declares_comes_from_the_python_side():
    """Tool names, argument names and the version the App declares to the host are
    substituted when the page is built. A placeholder left in the served page would be
    sent to the host as it is — a call to a tool named `__TOOL_...__`."""
    import pexafy_mcp

    js = widget._with_tool_names(widget._WIDGET_JS)
    assert f'new App({{ name: "Pexafy", version: "{pexafy_mcp.__version__}" }}' in js
    assert 'version: "__APP_VERSION__"' in widget._WIDGET_JS
    # The only double-underscore name left is the SDK's global, which is meant to be.
    assert set(re.findall(r"__[A-Z][A-Z_]+__", widget.GRID_HTML)) <= {"__PEXAFY_EXTAPPS__"}


def test_the_connection_probe_reads_the_structured_answer():
    """`connect_account` with `check_only` answers `{connected, budget}` in its
    structured content. Matching its English sentence instead broke on any rewording."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    watch = js[js.index("function watchForConnection()"):]
    watch = watch[:watch.index("\n}\n")]
    assert "const sc = (res && res.structuredContent) || {};" in watch
    assert "connected = sc.connected === true;" in watch
    assert "already searches with a Pexafy account" not in js
    assert "structuredContent.result" not in js


def test_the_way_back_costs_nothing():
    """The unfiltered page is kept beside the filtered one on the same trail step, so
    "Every shape" restores it instead of spending a second call to fetch what the widget
    is already holding. A filter you cannot undo without paying for it is a filter
    people do not try."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    body = js[js.index("async function applyShapes(list)"):]
    body = body[:body.index("\nshapeBtn.addEventListener")]
    back = body[body.index("if (!want.length) {"):]
    back = back[:back.index("\n  }")]
    assert "here.photos = here.base;" in back
    assert "callServerTool" not in back


def test_filtering_is_not_a_step_on_the_trail():
    """The trail records the photographs a reader chose to go deeper on. Choosing a
    format is not going anywhere — it is looking at the same place differently. Pushed
    on, six shape presses would have burnt the six refinements the reader actually has
    (MAX_REFINE)."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    body = js[js.index("async function applyShapes(list)"):]
    body = body[:body.index("\nshapeBtn.addEventListener")]
    assert "trail.push" not in body
    assert "here.shape = want;" in body


def test_the_filter_hides_itself_where_it_cannot_work():
    """Two ways it cannot: a host that will not proxy a tool call from the frame, and an
    answer with no replayable question behind it — a by-image search run from an
    uploaded file. A control that is present and inert is worse than one that is absent:
    the reader presses it, nothing happens, and they learn the widget is broken."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    body = js[js.index("function canShape()"):]
    body = body[:body.index("\n}")]
    assert "here.origin" in body and "canProxyTools()" in body
    assert "shapeWrap.hidden = true;" in js


def test_the_head_carries_the_filter_it_is_applying():
    """A reader who scrolls back to this grid ten messages later has to be able to tell
    why it holds nothing but vertical photographs. The state is on the button, not only
    inside the panel nobody has open."""
    js, css = widget._WIDGET_JS, widget._STYLE
    assert 'shapeBtn.classList.toggle("on", on.length > 0);' in js
    assert ".shapeb.on {" in css
    # One shape is its own outline; two are drawn together, nested on the one grid.
    assert 'shapeGl.innerHTML = shapesGlyph(on, 16, 2);' in js
    assert "function shapesGlyph(list, size, w)" in js


def test_the_menu_offers_the_three_shapes_and_the_way_out():
    """Three shapes and "Every shape" — the catalogue has exactly three, and a filter
    with no way back is a trap."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    assert 'const SHAPES = ["landscape", "portrait", "square"];' in js
    assert '"Every shape"' in js
    assert 'data-shape=""' in js


def test_several_shapes_can_be_on_at_once():
    """The filter is a SET, as the API declares it: "anything but portrait" is two shapes
    and they OR together. The rows are boxes to tick, the menu stays open for the next
    tick, and one call goes out for the lot — a reader ticking Landscape and then Square
    is asking one question, not two, so the call waits a beat for the second tick
    rather than spending an allowance on a page about to be replaced."""
    js, css = widget._WIDGET_JS, widget._STYLE
    assert "function toggleShape(shape)" in js and "let pending = null;" in js
    assert "const SHAPE_SETTLE_MS = 420;" in js
    assert 'role="menuitemcheckbox" aria-checked="' in js and 'role="menuitemradio"' in js
    assert ".shapeopt .sbox {" in css and ".shapeopt.on .sbox {" in css
    tog = js[js.index("function toggleShape(shape)"):js.index("async function applyShapes(list)")]
    assert "fillShapePop();" in tog                      # the tick shows at once
    assert "applyShapes(pending)" in tog and "SHAPE_SETTLE_MS" in tog
    # A tick made while a call was out is served when the call lands, never dropped.
    body = js[js.index("async function applyShapes(list)"):]
    body = body[:body.index("\nshapeBtn.addEventListener")]
    assert "if (pending !== null && !sameShapes(pending, here.shape)) applyShapes(pending);" in body
    # Whatever the tick order, one filter: the catalogue's own order, so two readers
    # who ticked the same shapes differently hold the same thing.
    assert "function shapeList(v)" in js and "SHAPES.filter(s => list.indexOf(s) !== -1)" in js
    # The state is a list everywhere it is written or read.
    assert "shape: normShapes(org.orientation)," in js
    assert "shape: shapeList(pg.shape)," in js
    assert "pg.shape = normShapes(pg.shape);" in js
    assert "shape: normShapes(ORIGIN && ORIGIN.shape)," in js
    # "Every shape" is still one press, and the menu closes on it: it is the way back.
    assert 'if (!shape) { applyShapes([]); return; }' in js


def test_all_three_shapes_is_every_shape():
    """Three ticks is no filter: the page already in hand, restored for nothing rather
    than fetched again under a filter that removes nothing. Measured otherwise —
    passing all three shapes puts the engine on its filtered path and the recall
    differs — so it is spelled [] and never sent."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    norm = js[js.index("function normShapes(v)"):js.index("function sameShapes(a, b)")]
    assert "list.length === SHAPES.length ? [] : list" in norm
    body = js[js.index("async function applyShapes(list)"):]
    body = body[:body.index("\nshapeBtn.addEventListener")]
    assert "const want = normShapes(list);" in body
    # The label and the toast name every shape on, in their own order.
    assert "function shapesLabel(list)" in js and 'join(" + ")' in js
    assert 'shapesLabel(want).toLowerCase().replace(/ \\+ /g, " or ")' in js


def test_an_empty_filtered_grid_says_which_empty_it_is():
    """Two different empties need two different sentences: the allowance ran out, or the
    catalogue holds no photograph of this shape for this question. The grid on screen
    stays either way — it is paid for and still useful."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    body = js[js.index("async function applyShapes(list)"):]
    body = body[:body.index("\nshapeBtn.addEventListener")]
    # The answer's whole allowance block: its state from `sc.budget`, its wording
    # from `_meta` (budgetOf).
    assert "const spent = budgetOf(sc);" in body
    assert 'spent.state === "exhausted"' in body
    assert "No " in body and "photos for this search" in body


def test_the_page_keeps_its_unfiltered_question_after_a_filter():
    """Pressing Portrait and then Landscape has to ask for landscapes, not for
    landscapes-that-are-also-portraits. The call overwrites ORIGIN through learnLinks,
    so the page's own origin is put back after every one of them."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    body = js[js.index("async function applyShapes(list)"):]
    body = body[:body.index("\nshapeBtn.addEventListener")]
    assert body.count("ORIGIN = here.origin;") >= 3     # success, empty, and the catch


def test_the_restore_reads_what_the_host_was_holding_when_the_frame_started():
    """The bug this closes made every reload come back to the bare anchor, trail and
    all — it was not the shape filter's, it only became visible through it.

    `restoreUi` runs at the END of render(), and render() has by then painted the grid,
    which finishes in capShown(), which SAVES. That save replaced `pexafyView` in the
    store with the state of the frame as it stood at that instant: one page, no
    refinements, no filter. The restore then read what it had just overwritten.
    Measured in the lab: a two-step trail came back as one step, every time.

    The snapshot is taken at module scope, before a line of the widget has run, and a
    snapshot cannot be overwritten by the thing it is a snapshot of."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    assert "\nconst BOOT_STATE = hostState();\n" in js       # module scope, run once
    body = js[js.index("function restoreUi()"):]
    body = body[:body.index("\n}\n")]
    assert "const boot = viewIn(BOOT_STATE);" in body
    assert "hostState()" not in body          # the live store is the overwritten one


def test_the_view_is_read_where_it_is_written_and_where_it_used_to_be():
    """The view state lives inside `privateContent`, the half of the store the model is
    not shown. The version before wrote it at the top level, next to `modelContent`, so
    a conversation left open across a deploy still has it there: both are read, the new
    place first. Whichever it came from, the next write puts it in the new place."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    view_in = js[js.index("function viewIn(st) {"):]
    view_in = view_in[:view_in.index("\n}\n")]
    assert "(priv && typeof priv === \"object\" && priv[UI_KEY]) || st[UI_KEY]" in view_in
    parts = js[js.index("function partsOf(st) {"):]
    parts = parts[:parts.index("\n}\n")]
    assert "view: viewIn(s)" in parts           # carried over, from either place
    assert "delete sel[UI_KEY];" in parts       # and never twice
    write = js[js.index("function storeWrite()"):js.index("function storePut(patch, now)")]
    assert "if (storeParts.view) priv[UI_KEY] = storeParts.view;" in write
    assert "privateContent: priv," in write


def test_a_shape_press_is_written_to_the_host_at_once():
    """Everything else here joins the 300ms queue, on the rule that nothing follows it
    which destroys the frame. A shape press breaks that rule twice: it is the one action
    in the widget that takes SECONDS, and the reader is free to do anything during them
    — including whatever makes the host re-parent the iframe. A coalesced write is a
    write that never happened, and this one costs a whole tool call to reproduce."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    body = js[js.index("async function applyShapes(list)"):]
    body = body[:body.index("\nshapeBtn.addEventListener")]
    assert body.count("saveUi(true);") == 2      # the filtered page, and the way back
    assert "saveUi();" not in body


def test_pexafys_own_page_can_take_over_the_selection_and_the_introduction():
    """The try-it page (/plugin) plays the host and does two of the grid's jobs itself:
    it ranks the liked photographs in a panel beside the conversation, and it runs the
    three-step introduction once per visitor, in its first grid only. It says so in its
    host context, under `pexafy` — a key the Apps SDK keeps (`passthrough`) — and a host
    that says nothing gets the grid exactly as before."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    opt = js[js.index("function embedOpt(name)"):js.index("/* A phone, in the host's own words.")]
    assert "(hostCtx() || {}).pexafy" in opt
    assert 'return embedOpt("selection") === "host";' in opt
    assert 'return embedOpt("coach") === false;' in opt
    # The strip under the grid stands down, and nothing else in it runs.
    sel = js[js.index("function paintSelBlock()"):js.index("/* ── The trail: refining")]
    assert sel.index("if (hostShowsSelection())") < sel.index("selEl.hidden = deckOpen")
    # Muted before the first paint of a result, so not even one frame of it shows.
    body = js[js.index("function render(sc) {"):]
    assert body.index("if (coachMuted()) coach.off = true;") < body.index("showStep();")
    # The receipt after a deck does not point at a strip that is not there.
    close = js[js.index("function closeViewer()"):js.index("/* The markup of one photo card")]
    assert "hostShowsSelection()" in close


def test_the_hosts_panel_can_drop_a_liked_photo():
    """A cross in the host's panel has to reach the grid, where the like lives: dropped
    only there, the photograph would come back with the next change of the selection.
    Only from the parent frame, and only through setKeptPhoto — the one path that also
    repaints the tile and tells the model."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    at = js.index('m.method !== "pexafy/unlike"')
    block = js[js.rindex('window.addEventListener("message"', 0, at):at + 300]
    assert "if (e.source !== window.parent) return;" in block
    assert "if (f) setKeptPhoto(f, false);" in block
