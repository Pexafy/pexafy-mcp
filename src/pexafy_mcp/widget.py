"""MCP App (UI) widget for the Pexafy search tools: an inline result grid.

Hosts show MCP `ImageContent` results in a collapsed tool panel, not inline. The MCP
Apps extension is the supported way to draw inline: the tool declares a `ui://`
resource in `_meta.ui.resourceUri`, and the host renders that HTML in a sandboxed
iframe in the conversation.

The sandbox blocks external scripts: an `import` from a CDN fails, the App never
initialises and the iframe stays at 0px. So the `@modelcontextprotocol/ext-apps`
browser bundle is vendored (assets/ext_apps_bundle.js) and inlined, its exports rebound
onto `globalThis` (what the official examples do with vite-plugin-singlefile).

The widget reads the tool's `structuredContent.data` (each photo carries a
server-signed `preview_url`, see previews.inject_preview_urls) and paints a grid of
unnumbered thumbnails, each with a heart that likes it and a mark for its shape. The
head carries a shape filter that re-runs the search from inside the frame; the liked
photos sit under the grid, numbered #1..#n. Tapping a tile opens a swipe deck in the
grid's place: one photo at a time, with its credit and caption, a ≈ button that re-runs
the by-image search from that photo, and a button that opens the photo's page on
Pexafy through the host (`app.openLink`). On a phone, and only there, the deck asks the
host for full screen. Thumbnails load from Pexafy's signed CDN, the one image origin
the resource CSP allows; the HMAC secret never reaches the iframe.
"""
from __future__ import annotations

import re
from pathlib import Path

from . import __version__, tooling, widget_i18n

# UI resource URI the search tools point at (tool `_meta.ui.resourceUri`).
GRID_URI = "ui://pexafy/grid.html"

# Fallback CDN used ONLY when the vendored bundle is missing (dev convenience).
_EXT_APPS_CDN = "https://esm.sh/@modelcontextprotocol/ext-apps@1.7.4"
EXT_APPS_ORIGIN = "https://esm.sh"

_BUNDLE_PATH = Path(__file__).resolve().parent / "assets" / "ext_apps_bundle.js"
_GLOBAL = "__PEXAFY_EXTAPPS__"


def _rebind_exports_to_global(js: str) -> str:
    """Turn the bundle's trailing ``export{local as Name,...}`` into a
    ``globalThis.__PEXAFY_EXTAPPS__={Name:local,...}`` assignment, so a second
    inline module can consume the SDK without any external fetch."""
    matches = list(re.finditer(r"export\s*\{([^}]*)\}", js))
    if not matches:
        return js  # no export map found — leave as-is (widget shows an error)
    m = matches[-1]
    pairs = []
    for entry in m.group(1).split(","):
        entry = entry.strip()
        if not entry:
            continue
        if " as " in entry:
            local, exported = (x.strip() for x in entry.split(" as "))
        else:
            local = exported = entry
        pairs.append(f"{exported}:{local}")
    assign = f"globalThis.{_GLOBAL}={{{','.join(pairs)}}};"
    return js[: m.start()] + assign + js[m.end():]


def _load_inline_sdk() -> str | None:
    try:
        js = _BUNDLE_PATH.read_text(encoding="utf-8")
    except OSError:
        return None
    # Safe inlining: neutralise any literal </script> (none today, future-proof).
    js = js.replace("</script", "<\\/script")
    return _rebind_exports_to_global(js)


_INLINE_SDK = _load_inline_sdk()
SDK_INLINED = _INLINE_SDK is not None


# Resource CSP needs the thumb CDN for images; the SDK is inlined (no CDN) unless the
# vendored bundle is missing, in which case esm.sh is needed for the fallback import.
RESOURCE_EXTRA_DOMAINS: list[str] = [] if SDK_INLINED else [EXT_APPS_ORIGIN]


# The dark palette, written once and injected into both dark selectors of _STYLE: the
# system preference, and a theme the host pushes (`data-theme` on <html>, set by
# applyDocumentTheme), which the media query alone would miss.
_DARK_TOKENS = """
    --fg: #e8e8ec; --muted: #a1a1aa;
    --surface: #18181b; --surface-2: #202027; --surface-3: #2a2a32;
    --border: rgba(255,255,255,.10); --border-strong: rgba(255,255,255,.20);
    --primary: #8b5cf6;
    --chip: rgba(255,255,255,.08); --primary-soft: rgba(139,92,246,.18);
    --warn: #fbbf24; --warn-soft: rgba(251,191,36,.16);
    --wall-a: rgba(139,92,246,.32); --wall-b: rgba(34,211,238,.20);
    --wall-c: rgba(255,255,255,.09);
    --shadow-card: 0 4px 14px rgba(0,0,0,.45);
    --shadow-lift: 0 16px 38px rgba(0,0,0,.6);
    /* The deck: the photo itself, blurred and darkened, is its ground. */
    --v-ink: #f4f4f6; --v-ink-2: rgba(255,255,255,.66); --v-ink-3: rgba(255,255,255,.42);
    --v-chip: rgba(255,255,255,.10); --v-chip-hi: rgba(255,255,255,.20);
    --v-back: #08080c;
    --v-btn: #23232c; --v-btn-ring: rgba(255,255,255,.10);
    --v-blur-b: .45; --v-blur-o: .55;
    --v-scrim: rgba(0,0,0,.35); --v-scrim-2: rgba(0,0,0,.86);
    --v-card-shadow: 0 26px 60px rgba(0,0,0,.55);
"""

_STYLE = """
  /* Pexafy design tokens, mirroring django_app/static/css. Dark values: _DARK_TOKENS. */
  :root { color-scheme: light dark;
    --fg: #18181b; --muted: #52525b;
    --surface: #ffffff; --surface-2: #f4f4f7; --surface-3: #eaeaef;
    --border: rgba(0,0,0,.08); --border-strong: rgba(0,0,0,.16);
    --primary: #7c3aed;
    --on-primary: #ffffff; --chip: rgba(0,0,0,.05); --primary-soft: rgba(124,58,237,.10);
    --keep: #10b981; --pass: #f43f5e;
    /* The budget mark: amber while searches are left, --pass once there are none. */
    --warn: #d97706; --warn-soft: rgba(217,119,6,.12);
    /* The shapes behind the wall: brand hues strong enough to survive the blur on
       white, too faint to read as photographs. */
    --wall-a: rgba(124,58,237,.20); --wall-b: rgba(6,182,212,.15);
    --wall-c: rgba(0,0,0,.075);
    /* The deck's verbs, in the colours swipe apps taught: rewind amber, skip red,
       ≈ blue, like green. Download is magenta, apart from the four. */
    --act-rewind: #f0ad1f; --act-pass: #fd5068; --act-sup: #12b8e8; --act-keep: #17c47f;
    --act-get: #db2777;
    /* The verb buttons are opaque plates; only the glyph is coloured. */
    --v-btn: #ffffff; --v-btn-ring: rgba(0,0,0,.06);
    /* The deck's palette: everything inside #viewer reads --v-*, so it follows the
       theme like the grid does. */
    --v-ink: #18181b; --v-ink-2: rgba(24,24,27,.62); --v-ink-3: rgba(24,24,27,.42);
    --v-chip: rgba(0,0,0,.06); --v-chip-hi: rgba(0,0,0,.13);
    --v-back: #ffffff;
    /* Light theme: the deck sits on plain white, like the grid (`--v-blur-o: 0` hides
       the blurred copy of the photo). The dark theme uses that copy as its ground. */
    --v-blur-b: 1; --v-blur-o: 0;
    --v-scrim: rgba(255,255,255,0); --v-scrim-2: rgba(255,255,255,0);
    --v-card-shadow: 0 22px 50px rgba(0,0,0,.28);
    --r-card: 14px; --r-btn: 12px;
    --shadow-card: 0 3px 12px rgba(0,0,0,.09);
    --shadow-lift: 0 14px 34px rgba(0,0,0,.18);
    /* The fill of the primary buttons: one brand colour, no gradient ("Use brand accent
       colors on primary buttons"; "Avoid custom gradients or patterns", OpenAI's UI
       guidelines). The same in both themes: white on it reads 5.7:1, where the dark
       theme's lighter violet gives 4.2:1. */
    --brand: #7c3aed;
    /* The platform's own typeface: "Don't use custom fonts, even in full screen modes"
       (OpenAI's UI guidelines). */
    --font: system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto,
      'Helvetica Neue', Arial, sans-serif; }
  @media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { __DARK__ } }
  :root[data-theme="dark"] { __DARK__ }

  * { box-sizing: border-box; }
  [hidden] { display: none !important; }
  html, body { margin: 0; }
  /* `clip`: a page turn slides the grid past the edge without a horizontal
     scrollbar, and unlike `overflow-x: hidden` it does not turn the body into a
     scroll container on the other axis. */
  body { position: relative; min-height: 64px; background: transparent; color: var(--fg);
         overflow-x: clip;
         font-family: var(--font); -webkit-font-smoothing: antialiased;
         -webkit-tap-highlight-color: transparent; }
  #status { padding: 16px 18px; font-size: 13px; line-height: 1.5; color: var(--muted); }
  #status.err { color: #dc2626; }
  svg { display: block; }
  @media (prefers-reduced-motion: reduce) { * { animation-duration: .01ms !important;
    transition-duration: .01ms !important; } }

  /* ── The coach: three first steps, one at a time (COACH_STEPS) ──────────── */
  .gridtip { margin: 9px 12px 0; padding: 8px 12px 9px; border-radius: 12px;
             background: var(--surface-2); font-size: 12px; line-height: 1.5;
             color: var(--muted); display: flex; align-items: center;
             justify-content: center; gap: 10px; text-align: center; flex-wrap: wrap;
             animation: rise .3s ease .2s backwards; }
  .gridtip > span { min-width: 0; }
  /* The heart and ≈ sit inside the sentence, inline on its baseline. */
  .gridtip b { font-weight: 600; color: var(--fg); }
  .gridtip svg { display: inline-block; vertical-align: -2px; }
  /* Numbered dots joined by a line: done ones filled, the next one blinking. Small
     enough to share the sentence's line at a phone's width. */
  .gridtip .steps { display: inline-flex; align-items: center; flex: 0 0 auto; }
  .gridtip .st { width: 18px; height: 18px; border-radius: 50%; display: grid;
                 place-items: center; font-size: 10px; font-weight: 700; font-style: normal;
                 border: 1.5px solid var(--border); color: var(--muted);
                 background: var(--surface); transition: background .25s ease,
                 border-color .25s ease, color .25s ease; }
  .gridtip .st svg { vertical-align: 0; }
  .gridtip .st.done { background: var(--primary); border-color: var(--primary); color: #fff; }
  /* Without end: a few beats and a stop went unnoticed. */
  @keyframes stepblink {
    0%, 100% { background: var(--primary); color: #fff;
               box-shadow: 0 0 0 0 color-mix(in srgb, var(--primary) 60%, transparent); }
    50%      { background: var(--surface); color: var(--primary);
               box-shadow: 0 0 0 6px transparent; }
  }
  .gridtip .st.now { border-color: var(--primary); color: var(--primary);
                     animation: stepblink 1.1s ease-in-out infinite; }
  .gridtip .sl { width: 16px; height: 1.5px; background: var(--border);
                 transition: background .25s ease; }
  .gridtip .sl.done { background: var(--primary); }
  .gridtip .say { animation: rise .28s ease; text-wrap: balance; }
  .gridtip.done .say { color: var(--primary); font-weight: 600; }
  /* The cross rides with the sentence, so the centred line stays centred. */
  .gridtip .tipx { flex: 0 0 auto; width: 20px; height: 20px; padding: 0;
                   display: grid; place-items: center; border: 0; cursor: pointer;
                   border-radius: 50%; background: none; color: var(--muted); }
  .gridtip .tipx:hover { background: var(--surface-2); color: var(--fg); }
  /* The control the line names blinks until it is used: a ring on the tile, the
     heart or the deck's verb. On and off, because a steady ring on a photograph
     reads as a selected tile. The tile also lifts, three times (scheduleNudge). */
  @keyframes tilenudge {
    30% { transform: translateY(-5px) scale(1.012); }
    60% { transform: translateY(0) scale(1); }
  }
  .card.nudge { animation: tilenudge .62s cubic-bezier(.2,.8,.2,1) .45s; }
  @keyframes coachblink {
    0%, 100% { box-shadow: 0 0 0 3px var(--primary),
                           0 0 16px 3px color-mix(in srgb, var(--primary) 55%, transparent); }
    50%      { box-shadow: 0 0 0 3px color-mix(in srgb, var(--primary) 8%, transparent),
                           0 0 0 0 transparent; }
  }
  .card.coach { box-shadow: 0 0 0 3px var(--primary);
                animation: coachblink 1.1s ease-in-out infinite; }
  /* Both in one list: two rules each setting `animation` would drop the lift. */
  .card.coach.nudge { animation: coachblink 1.1s ease-in-out infinite,
                                 tilenudge .62s cubic-bezier(.2,.8,.2,1) .45s; }
  /* The like step's heart keeps its dark plate: filled violet is what a LIKED heart
     looks like. A white ring and a beat instead, on the tile likeTarget picks. */
  @keyframes pickblink {
    0%, 100% { transform: scale(1);
               box-shadow: 0 0 0 2px rgba(255,255,255,.95), 0 0 0 5px var(--primary); }
    50%      { transform: scale(1.22);
               box-shadow: 0 0 0 2px rgba(255,255,255,0), 0 0 0 11px transparent; }
  }
  .card .pick.coach { box-shadow: 0 0 0 2px #fff, 0 0 0 5px var(--primary);
                      animation: pickblink 1s ease-in-out infinite; }
  /* The deck's verb: a 3px ring outside the button, on and off every second. */
  @keyframes verbblink {
    0%, 100% { opacity: 1; transform: scale(1); }
    50%      { opacity: .08; transform: scale(1.1); }
  }
  .vbig.coach { position: relative; }
  .vbig.coach::after { content: ""; position: absolute; inset: -7px; border-radius: 50%;
                       border: 3px solid var(--primary); pointer-events: none;
                       box-shadow: 0 0 14px color-mix(in srgb, var(--primary) 55%, transparent);
                       animation: verbblink 1s ease-in-out infinite; }
  /* The line over the deck's verb, placed by placeGuide (`left` and `--ax`), never
     by a transform, which the entrance animates. `max-content` stops it folding
     early against the frame; `balance` evens it out when it must. */
  .vguide { position: absolute; bottom: calc(100% + 18px); left: 0; z-index: 5;
            width: max-content; max-width: 360px; text-align: center; text-wrap: balance;
            line-height: 1.35; padding: 7px 13px; border-radius: 14px; font-size: 12px;
            font-weight: 600; color: #fff; background: var(--primary);
            box-shadow: 0 6px 18px rgba(0,0,0,.28); pointer-events: none;
            animation: guidein .3s ease .12s backwards; }
  @keyframes guidein { from { opacity: 0; transform: translateY(6px); }
                       to   { opacity: 1; transform: none; } }
  .vguide::after { content: ""; position: absolute; top: 100%; left: var(--ax, 50%);
                   margin-left: -5px; border: 5px solid transparent;
                   border-top-color: var(--primary); }
  /* Inline glyphs: an svg is a block in this sheet and would break the line. */
  .vguide b { display: inline; }
  .vguide svg { display: inline-block; vertical-align: -2px; }
  @media (max-width: 420px) { .vguide { max-width: 250px; } }
  /* Swipe right, shown: the like stamp breathes on the card the heart is about. */
  @keyframes stamphint { 0%, 100% { opacity: 0; } 50% { opacity: .55; } }
  .vcard.top.hintkeep:not(.drag) .vstamp.keep { animation: stamphint 1.5s ease-in-out .6s 2; }
  /* Under reduced motion every ring still shows, standing still. */
  @media (prefers-reduced-motion: reduce) {
    .card.nudge, .card.coach, .card.coach.nudge, .card .pick.coach, .vbig.coach::after,
    .gridtip .st.now, .vguide, .vcard.top.hintkeep:not(.drag) .vstamp.keep { animation: none; }
  }

  /* ── The head: wordmark, trail, shape filter, account ─────────────────────
     Three columns. The trail owns the middle one, centred on the whole width, where
     it reads as a control rather than as part of the logo; the outer columns are
     `minmax(0, 1fr)` so a long trail takes room from the right before the name. */
  .gridhead { display: grid; grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr);
              align-items: center; padding: 10px 12px 3px; position: relative; }
  /* The name, top left: 700 and -.03em are `.site-logo`'s own values
     (django_app/static/css/layout.css). Plain text in the ink colour, not the logo's
     gradient: "Do not include your logo as part of the response" (OpenAI's UI
     guidelines; the host already shows the app's logo and name). */
  .brandmark { justify-self: start; border: 0; padding: 0; cursor: pointer;
               font-family: var(--font); font-size: 20px; font-weight: 700;
               letter-spacing: -.03em; line-height: 1.15;
               background: none; color: var(--fg);
               transition: color .16s ease, transform .16s cubic-bezier(.2,.8,.2,1); }
  .brandmark:hover { color: var(--primary); transform: translateY(-1px); }
  .brandmark:active { transform: none; }

  /* The trail: the anchor (the search the assistant ran), then one thumbnail per
     photo the reader asked more like. Every chip is a way back. `safe center` lets a
     trail too wide to fit scroll from its start instead of cutting off the anchor.
     `overflow-x: auto` computes the other axis as `auto` too, so the padding is wider
     than the count badge hangs (6px), or its top would be clipped. */
  .trail { grid-column: 2; justify-self: center; display: flex; align-items: center;
           max-width: 100%; justify-content: center; justify-content: safe center;
           padding: 9px 9px 5px; overflow-x: auto;
           scrollbar-width: none; -ms-overflow-style: none;
           animation: rise .22s cubic-bezier(.2,.8,.2,1); }
  .trail::-webkit-scrollbar { display: none; }
  .tsep { flex: 0 0 auto; width: 9px; height: 1.5px; border-radius: 2px;
          background: var(--border-strong); }
  /* `overflow: visible` so the count badge sits on the corner; the picture is
     clipped by its own box (.tw), which keeps it inside the border. */
  .tstep { position: relative; flex: 0 0 auto; display: grid; place-items: center;
           padding: 0; cursor: pointer; overflow: visible;
           width: 26px; height: 26px; border-radius: 8px;
           border: 1px solid var(--border-strong); background: var(--surface-2);
           color: var(--muted);
           transition: transform .16s cubic-bezier(.2,.8,.2,1), box-shadow .16s ease,
                       border-color .16s ease, color .16s ease; }
  .tw { display: block; width: 100%; height: 100%; border-radius: 7px; overflow: hidden; }
  .tstep img { width: 100%; height: 100%; object-fit: cover; display: block; }
  /* How many of that page's photos are liked. */
  .tn { position: absolute; top: -6px; right: -6px; min-width: 15px; height: 15px;
        padding: 0 3px; display: grid; place-items: center; border-radius: 999px;
        font-size: 9.5px; font-weight: 800; font-variant-numeric: tabular-nums;
        color: var(--on-primary); background: var(--primary);
        border: 1.5px solid var(--surface); pointer-events: none; }
  .tstep:hover { transform: translateY(-1px); border-color: var(--primary);
                 color: var(--primary); }
  .tstep:focus-visible { outline: 2px solid var(--primary); outline-offset: 2px; }
  /* The step on screen: a ring, not a fill, which would tint the thumbnail. */
  .tstep.on { border-color: var(--primary); color: var(--primary);
              box-shadow: 0 0 0 2px var(--primary-soft); }
  .tstep.anchor { border-radius: 50%; background: var(--chip); }
  /* The anchor beats three times when the trail grows (beatTheAnchor), then stops. */
  @keyframes anchorbeat {
    0%, 100% { transform: none;        box-shadow: 0 0 0 0 var(--primary-soft); }
    35%      { transform: scale(1.16); box-shadow: 0 0 0 7px transparent; }
  }
  .tstep.anchor.hint { color: var(--primary); border-color: var(--primary);
                       animation: anchorbeat 1.15s ease-in-out 3; }
  @media (prefers-reduced-motion: reduce) { .tstep.anchor.hint { animation: none; } }

  /* ── Right end of the head: the shape filter and the account button ─────── */
  /* Each control keeps `position: relative`, so its panel hangs under it. */
  .headacts { grid-column: 3; justify-self: end; display: inline-flex;
              align-items: center; gap: 7px; }
  .acctwrap { position: relative; }
  /* The bug report: the same 30px circle, next to the account. It opens the contact
     form on "Bug reports". */
  .bugb { position: relative; display: grid; place-items: center;
          width: 30px; height: 30px; padding: 0; cursor: pointer;
          border-radius: 999px; border: 1px solid var(--border-strong);
          background: var(--surface-2); color: var(--muted);
          transition: color .16s ease, border-color .16s ease, background .16s ease,
                      transform .16s cubic-bezier(.2,.8,.2,1); }
  .bugb:hover { color: var(--primary); border-color: var(--primary); transform: translateY(-1px); }
  .bugb:active { transform: none; }
  .bugb:focus-visible { outline: 2px solid var(--primary); outline-offset: 2px; }
  /* Under the name, centred, on every grid: where the photos the reader likes go. The
     site's violet, bold; its cross closes it for the grid on screen. */
  .sharenote { grid-column: 1 / -1; justify-self: center; display: inline-flex;
               align-items: center; gap: 6px; max-width: 100%; margin: 7px 0 1px;
               font-size: 12.5px; font-weight: 700; line-height: 1.35;
               color: var(--primary); text-align: center; }
  .sharenote span { text-wrap: balance; }
  .sharex { flex: 0 0 auto; width: 20px; height: 20px; display: grid; place-items: center;
            border: 0; padding: 0; cursor: pointer; border-radius: 999px;
            background: none; color: var(--muted);
            transition: color .14s ease, background .14s ease; }
  .sharex:hover { color: var(--fg); background: var(--surface-2); }
  .sharex:focus-visible { outline: 2px solid var(--primary); outline-offset: 1px; }
  /* Right-to-left text (Arabic, Persian, Urdu) sets its own direction, line by line,
     in a layout that stays as it is. */
  #status, #toast, .sharenote > span, .say, .seltitle > span, .selhead > span, .selhint,
  .wt, .wd, .lc-t, .lc-d, .ap-t, .ap-d, .moreb, .brandcta span, .vinfo .by > span,
  .vinfo .cap, .vguide { unicode-bidi: plaintext; }
  /* The deck's coach line sits over the bottom of the photo: the caption under it steps
     aside while it shows (paintDeckCoach), the credit stays. */
  #viewer.guiding .vinfo .cap { visibility: hidden; }

  /* The shape filter: the same 30px circle as the account button beside it. It
     re-asks the question with the shape attached (applyShapes) instead of hiding
     tiles, and it is hidden wherever it cannot work (canShape). */
  .shapewrap { position: relative; }
  .shapeb { position: relative; display: grid; place-items: center;
            width: 30px; height: 30px; padding: 0; cursor: pointer;
            border-radius: 999px; border: 1px solid var(--border-strong);
            background: var(--surface-2); color: var(--muted);
            transition: color .16s ease, border-color .16s ease, background .16s ease,
                        transform .16s cubic-bezier(.2,.8,.2,1); }
  .shapeb:hover { color: var(--primary); border-color: var(--primary);
                  transform: translateY(-1px); }
  .shapeb:active { transform: none; }
  .shapeb:focus-visible { outline: 2px solid var(--primary); outline-offset: 2px; }
  /* A filter that is on says so on the button, not only inside the menu. */
  .shapeb.on { color: var(--primary); border-color: var(--primary);
               background: var(--primary-soft); }
  .sbglyph { display: grid; place-items: center; }
  .shapepop { position: absolute; top: calc(100% + 8px); right: 0; z-index: 40;
              width: 186px; padding: 6px; text-align: left;
              border-radius: var(--r-card); border: 1px solid var(--border);
              background: var(--surface); box-shadow: var(--shadow-lift); }
  .shapeopt { display: flex; align-items: center; gap: 9px; width: 100%;
              padding: 8px 9px; cursor: pointer; border: 0; border-radius: 9px;
              background: transparent; color: var(--fg); font-family: var(--font);
              font-size: 13px; font-weight: 550; text-align: left;
              transition: background .14s ease, color .14s ease; }
  .shapeopt:hover { background: var(--surface-2); }
  .shapeopt:focus-visible { outline: 2px solid var(--primary); outline-offset: -2px; }
  .shapeopt .sgl { flex: 0 0 auto; display: grid; place-items: center;
                   width: 18px; height: 18px; color: var(--muted); }
  .shapeopt.on { color: var(--primary); }
  .shapeopt.on .sgl { color: var(--primary); }
  /* A box per shape, empty or ticked: several shapes can be on at once. */
  .shapeopt .sbox { margin-left: auto; width: 16px; height: 16px; flex: 0 0 auto;
                    display: grid; place-items: center; border-radius: 4px;
                    border: 1.5px solid var(--border); color: #fff;
                    transition: background .15s ease, border-color .15s ease; }
  .shapeopt.on .sbox { background: var(--primary); border-color: var(--primary); }
  .shapeopt .stick { margin-left: auto; display: grid; place-items: center;
                     color: var(--primary); }
  .shapeopt[disabled] { opacity: .5; cursor: default; }
  /* A rule between "Every shape", the way back, and the three shapes. */
  .shapesep { height: 1px; margin: 5px 7px; background: var(--border); }
  @media (max-width: 420px) { .shapepop { width: auto; left: auto; right: 0; min-width: 170px; } }
  /* The account button, on every answer, where sites put it. It carries a mark when
     there is something to say (paintAccount), and the panel under it says it. */
  .acct { position: relative; display: grid; place-items: center;
          width: 30px; height: 30px; padding: 0; cursor: pointer;
          border-radius: 999px; border: 1px solid var(--border-strong);
          background: var(--surface-2); color: var(--muted);
          transition: color .16s ease, border-color .16s ease, background .16s ease,
                      transform .16s cubic-bezier(.2,.8,.2,1); }
  .acct:hover { color: var(--primary); border-color: var(--primary);
                transform: translateY(-1px); }
  .acct:active { transform: none; }
  .acct:focus-visible { outline: 2px solid var(--primary); outline-offset: 2px; }
  /* Signed in is a state: the button leads back to the site, and offers nothing. */
  .acct.in { color: var(--primary); border-color: var(--primary);
             background: var(--primary-soft); }
  .acct.warn { color: var(--warn); border-color: var(--warn); background: var(--warn-soft); }
  .acct.spent { color: var(--pass); border-color: var(--pass); background: rgba(244,63,94,.12); }
  /* The mark: amber while searches are left, red once none are. It beats three times
     when it appears, then holds: this is a notice, not an alarm. */
  .aglyph { display: grid; place-items: center; }
  .dot { position: absolute; top: -1px; right: -1px; width: 9px; height: 9px;
         border-radius: 999px; background: var(--warn);
         border: 2px solid var(--surface); }
  .acct.spent .dot { background: var(--pass); }
  @keyframes dotbeat {
    0%, 100% { transform: none;        box-shadow: 0 0 0 0 var(--warn-soft); }
    40%      { transform: scale(1.35); box-shadow: 0 0 0 6px transparent; }
  }
  .dot.hint { animation: dotbeat 1.1s ease-in-out 3; }
  @media (prefers-reduced-motion: reduce) { .dot.hint { animation: none; } }

  /* The panel under the button, aligned to the right edge: centred on a button in
     the corner, it would hang off the card. */
  .acctpop { position: absolute; top: calc(100% + 8px); right: 0; z-index: 40;
             width: 230px; padding: 12px 13px 13px; text-align: left;
             border-radius: var(--r-card); border: 1px solid var(--border);
             background: var(--surface); box-shadow: var(--shadow-lift);
             animation: rise .18s cubic-bezier(.2,.8,.2,1); }
  .ap-t { margin: 0; font-size: 13px; font-weight: 650; line-height: 1.3; color: var(--fg); }
  .ap-d { margin: 5px 0 0; font-size: 12px; line-height: 1.45; color: var(--muted); }
  /* One button for the panel, the last-call strip and the wall: the same door
     wherever it is met. */
  .gobtn { margin-top: 11px; width: 100%; padding: 8px 12px; cursor: pointer;
           border: 0; border-radius: var(--r-btn); font-family: var(--font);
           font-size: 12.5px; font-weight: 650; letter-spacing: -.01em;
           color: var(--on-primary); background: var(--brand);
           box-shadow: 0 6px 16px rgba(124,58,237,.30);
           transition: transform .16s cubic-bezier(.2,.8,.2,1), box-shadow .16s ease; }
  .gobtn:hover { transform: translateY(-1px); box-shadow: 0 10px 22px rgba(124,58,237,.40); }
  .gobtn:active { transform: none; }
  .gobtn:focus-visible { outline: 2px solid var(--primary); outline-offset: 3px; }
  /* Narrow: the panel spans the head instead of hanging off one side. */
  @media (max-width: 420px) { .acctpop { width: auto; left: 0; right: 0; } }

  /* The last searches of an allowance, announced over a grid of results while there
     still is one: a refusal only becomes the wall where the host mounts the widget
     again, and not every host does. */
  .lastcall { display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
              margin: 6px 12px 0; padding: 12px 14px; border-radius: var(--r-card);
              border: 1px solid var(--warn); background: var(--warn-soft);
              animation: rise .24s cubic-bezier(.2,.8,.2,1); }
  .lc-icon { flex: 0 0 auto; display: grid; place-items: center;
             width: 30px; height: 30px; border-radius: 999px;
             color: var(--warn); background: var(--surface); }
  .lc-text { flex: 1 1 190px; min-width: 0; }
  .lc-t { margin: 0; font-size: 13px; font-weight: 650; line-height: 1.3; color: var(--fg); }
  .lc-d { margin: 3px 0 0; font-size: 12px; line-height: 1.45; color: var(--muted); }
  .lastcall .gobtn { flex: 0 0 auto; width: auto; margin: 0; padding: 8px 16px; }

  /* ── The wall (paintWall) ─────────────────────────────────────────────────
     One grid cell holding both layers, so the card keeps its own transform for the
     entrance and the block keeps the height of its content. */
  .wall { display: grid; }
  .wall > * { grid-area: 1 / 1; }
  /* The result grid's own geometry, gridFirst() cells of it: the widget keeps its
     size between an answer and a refusal. */
  .wallblur { display: grid; gap: 10px; padding: 12px;
              grid-template-columns: repeat(auto-fill, minmax(max(140px, (100% - 20px) / 3), 1fr));
              filter: blur(9px); opacity: .9; pointer-events: none; }
  .wallblur i { display: block; aspect-ratio: 1 / 1; border-radius: var(--r-card);
                background: linear-gradient(150deg, var(--wall-c), var(--wall-b)); }
  .wallblur i:nth-child(3n+1) { background: linear-gradient(140deg, var(--wall-a), var(--wall-c)); }
  .wallblur i:nth-child(3n+2) { background: linear-gradient(200deg, var(--wall-b), var(--wall-a)); }
  .wallcard { z-index: 1; place-self: center; width: min(330px, calc(100% - 40px));
              padding: 20px 18px; text-align: center;
              border-radius: var(--r-card); border: 1px solid var(--border);
              background: var(--surface); box-shadow: var(--shadow-lift);
              animation: rise .24s cubic-bezier(.2,.8,.2,1); }
  .wallcard .wicon { display: grid; place-items: center; width: 38px; height: 38px;
                     margin: 0 auto 11px; border-radius: 999px;
                     color: var(--warn); background: var(--warn-soft); }
  /* The wall lifting is good news, in green. */
  .wallcard.back .wicon { color: var(--keep); background: rgba(16,185,129,.14); }
  .wallcard.spent .wicon { color: var(--pass); background: rgba(244,63,94,.12); }
  .wt { margin: 0; font-size: 15px; font-weight: 700; letter-spacing: -.01em;
        line-height: 1.3; color: var(--fg); }
  .wd { margin: 7px 0 0; font-size: 12.5px; line-height: 1.5; color: var(--muted); }
  .wallcard .gobtn { margin-top: 14px; }
  /* The second way out: a link under the button, not a rival to it. */
  .walllink { display: block; margin: 10px auto 0; padding: 4px 8px; border: 0;
              background: none; cursor: pointer; font-family: var(--font);
              font-size: 12px; color: var(--muted); text-decoration: underline;
              text-underline-offset: 2px; }
  .walllink:hover { color: var(--fg); }
  .walllink:focus-visible { outline: 2px solid var(--primary); outline-offset: 2px; }

  /* ── Result grid ───────────────────────────────────────────────────────── */
  /* Three columns at most: a tile must answer "is this the photograph?", which about
     130px (four across a chat column) cannot. The track minimum is the larger of 140px
     and a third of the row, gaps deducted; below ~460px it falls to two columns.
     `pan-y` leaves vertical scrolling to the conversation and gives the horizontal
     axis to the page-turn gesture. */
  .grid { display: grid; gap: 10px; padding: 12px;
          grid-template-columns: repeat(auto-fill, minmax(max(140px, (100% - 20px) / 3), 1fr));
          touch-action: pan-y; will-change: transform; transition: opacity .2s ease; }
  /* A refinement in flight: the grid it replaces stays, dimmed, with a bar sweeping
     under the head, so the reader keeps their place. */
  .grid.busy { opacity: .45; pointer-events: none; }
  .gridhead.busy::after { content: ""; position: absolute; left: 12px; right: 12px;
                          bottom: -3px; height: 2px; border-radius: 2px;
                          background: var(--brand);
                          animation: sweep 1.05s ease-in-out infinite; }
  @keyframes sweep { 0%   { transform: scaleX(.12); transform-origin: left; }
                     50%  { transform: scaleX(1);   transform-origin: left; }
                     50.1%{ transform: scaleX(1);   transform-origin: right; }
                     100% { transform: scaleX(.12); transform-origin: right; } }
  .card { position: relative; aspect-ratio: 1 / 1; border-radius: var(--r-card);
          overflow: hidden; cursor: zoom-in; background: var(--surface-2);
          border: 1px solid var(--border); box-shadow: var(--shadow-card);
          transition: transform .18s cubic-bezier(.2,.8,.2,1), box-shadow .18s ease; }
  .card:hover { transform: translateY(-3px); box-shadow: var(--shadow-lift); }
  .card:focus-visible { outline: 2px solid var(--primary); outline-offset: 2px; }
  /* An <img> is natively draggable, and a native drag cancels the page-turn gesture
     two frames in. The `draggable` attribute is set too, for Firefox. */
  .card img, .seltile img { user-select: none; -webkit-user-drag: none; }
  .card img { width: 100%; height: 100%; object-fit: cover; display: block;
              transition: transform .45s cubic-bezier(.2,.8,.2,1); }
  .card:hover img { transform: scale(1.06); }
  /* A soft scrim only where text sits — the picture keeps its own contrast. */
  .card .veil { position: absolute; inset: 0; pointer-events: none;
                background: linear-gradient(rgba(0,0,0,.34) 0, transparent 26%,
                            transparent 58%, rgba(0,0,0,.6) 100%);
                opacity: .9; transition: opacity .2s ease; }
  /* Tiles carry no number: the only ranking is the reader's own, on the liked photos
     under the grid (.seltile .rk). */
  .card .src { position: absolute; top: 7px; right: 7px; padding: 2px 8px; font-size: 9px;
               font-weight: 600; letter-spacing: .3px; color: rgba(255,255,255,.92);
               border-radius: 999px; background: rgba(0,0,0,.34);
               backdrop-filter: blur(8px); -webkit-backdrop-filter: blur(8px);
               opacity: 0; transform: translateY(-3px); transition: opacity .18s ease, transform .18s ease; }
  .card:hover .src, .card:focus-within .src { opacity: 1; transform: none; }
  /* The ≈ badge on the photo a refinement was asked from; the source chip stands down
     on that tile, since both use the top-right corner. */
  .card .cstar { position: absolute; top: 6px; right: 6px; width: 21px; height: 21px;
                 display: grid; place-items: center; border-radius: 50%; color: #fff;
                 background: var(--act-sup); box-shadow: 0 2px 8px rgba(0,0,0,.34);
                 border: 1.5px solid rgba(255,255,255,.85); }
  .card .by { position: absolute; left: 9px; right: 40px; bottom: 9px; font-size: 11px; font-weight: 600;
              letter-spacing: .1px; color: #fff; white-space: nowrap; overflow: hidden;
              text-overflow: ellipsis; text-shadow: 0 1px 3px rgba(0,0,0,.5); }
  /* The photo's shape, drawn as the shape itself, top left. Always on screen (a
     hover-only mark does not exist on a phone), white over the veil, at .72 so it
     does not compete with the picture. */
  .card .shape { position: absolute; top: 8px; left: 9px; display: grid; place-items: center;
                 color: #fff; opacity: .72; pointer-events: none;
                 filter: drop-shadow(0 1px 3px rgba(0,0,0,.55));
                 transition: opacity .18s ease; }
  .card:hover .shape, .card:focus-within .shape { opacity: .95; }
  /* The heart: the deck's like, without opening it. Always on screen, since a phone
     has no hover, on a dark plate that holds over any photograph. */
  .card .pick { position: absolute; right: 6px; bottom: 6px; width: 30px; height: 30px;
                display: grid; place-items: center; border: 0; padding: 0; cursor: pointer;
                border-radius: 50%; color: #fff; background: rgba(0,0,0,.46);
                backdrop-filter: blur(8px); -webkit-backdrop-filter: blur(8px);
                box-shadow: 0 1px 6px rgba(0,0,0,.28);
                opacity: 1; transform: none; transition: opacity .16s ease,
                transform .16s cubic-bezier(.2,.8,.2,1), background .16s ease; }
  .card:hover .pick, .card:focus-within .pick { transform: scale(1.08); }
  .card .pick:hover { background: rgba(0,0,0,.62); }
  .card.on { box-shadow: 0 0 0 2.5px var(--primary), var(--shadow-lift); }
  .card.on .pick { background: var(--primary); }
  .card.on .veil { opacity: .5; }
  .card.dead img, .vcard.dead img { display: none; }
  .card.dead { background: linear-gradient(135deg, var(--surface-2), var(--surface-3)); }
  .vcard.dead::after { content: var(--t-dead, "Preview unavailable"); padding: 10px 16px; border-radius: 999px;
                       font-size: 12px; font-weight: 600; color: var(--v-ink-2);
                       background: var(--v-chip); }
  @keyframes pulse { 50% { transform: scale(1.22); } }
  .card.on .pick svg { animation: pulse .3s ease; }

  @keyframes rise { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: none; } }

  /* ── Under the grid: one slot, one control ─────────────────────────────── */
  /* "See N more" while tiles are hidden (GRID_PAGES), then "Open in Pexafy". */
  .gridmore { display: flex; justify-content: center; padding: 5px 12px 17px; }
  .moreb { display: inline-flex; align-items: center; gap: 7px; cursor: pointer;
           border: 1px solid var(--border-strong); background: var(--surface);
           padding: 7px 15px; border-radius: 999px; font-family: var(--font);
           font-size: 12.5px; font-weight: 600; color: var(--fg);
           box-shadow: var(--shadow-card);
           transition: transform .14s cubic-bezier(.2,.8,.2,1), border-color .16s ease,
                       color .16s ease; }
  .moreb:hover { transform: translateY(-1px); border-color: var(--primary);
                 color: var(--primary); }
  .moreb:focus-visible { outline: 2px solid var(--primary); outline-offset: 2px; }

  /* The liked photos, as a grid of their own under the results: each tile carries
     its rank and the cross that drops it, and any tile reopens the deck over the
     liked photos alone. */
  .selblock { margin: 0 12px; padding: 12px 0 14px; border-top: 1px solid var(--border);
              animation: rise .22s cubic-bezier(.2,.8,.2,1); }
  /* The title says what the block is for, with the heart that filled it. */
  .seltitle { margin: 0; padding: 0 1px 7px; display: flex; align-items: flex-start; gap: 7px;
              font-size: 13px; font-weight: 700; line-height: 1.35; color: var(--fg); }
  .seltitle span { text-wrap: balance; }
  /* On the first line when the title folds, like a bullet. */
  .seltitle svg { flex: 0 0 auto; margin-top: 2px; color: var(--primary); }
  .selhead { display: flex; align-items: center; justify-content: space-between;
             gap: 10px; flex-wrap: wrap; padding: 0 1px 9px;
             font-size: 12.5px; color: var(--muted); }
  .selhead b { font-size: 13.5px; font-weight: 800; color: var(--primary);
               font-variant-numeric: tabular-nums; }
  .selacts { display: inline-flex; align-items: center; gap: 8px; }
  /* Six across a chat column: small enough to list what is held, big enough to tell
     two photographs apart. A grid that grows downwards, never a scrolling box. */
  .selgrid { display: grid; gap: 10px;
             grid-template-columns: repeat(auto-fill, minmax(max(70px, (100% - 50px) / 6), 1fr)); }
  /* `overflow: visible`: the cross sits on the corner; the picture is clipped by its
     own radius. */
  .seltile { position: relative; aspect-ratio: 1 / 1; cursor: zoom-in; border: 0;
             padding: 0; background: none;
             transition: transform .16s cubic-bezier(.2,.8,.2,1); }
  .seltile img { width: 100%; height: 100%; object-fit: cover; display: block;
                 border-radius: 11px; border: 1px solid var(--border);
                 box-shadow: var(--shadow-card); }
  .seltile:hover { transform: translateY(-2px); }
  .seltile:hover img { box-shadow: var(--shadow-lift); }
  .seltile:focus-visible { outline: 2px solid var(--primary); outline-offset: 3px;
                           border-radius: 11px; }
  /* The rank: #1 first, in the order liked unless the reader dragged them into another.
     It is what the assistant is told and what
     the reader says back ("use #2 for the header"). */
  .seltile .rk { position: absolute; top: 4px; left: 4px; min-width: 19px; height: 17px;
                 padding: 0 5px; display: inline-flex; align-items: center;
                 justify-content: center; font-size: 10px; font-weight: 700;
                 font-variant-numeric: tabular-nums; color: #fff; border-radius: 999px;
                 background: rgba(0,0,0,.5); backdrop-filter: blur(8px) saturate(1.4);
                 -webkit-backdrop-filter: blur(8px) saturate(1.4);
                 border: 1px solid rgba(255,255,255,.24); pointer-events: none; }
  .seltile .x { position: absolute; top: -6px; right: -6px; width: 20px; height: 20px;
                display: grid; place-items: center; border: 0; padding: 0; cursor: pointer;
                border-radius: 50%; color: #fff; background: var(--act-pass);
                box-shadow: 0 2px 7px rgba(0,0,0,.35);
                border: 1.5px solid var(--surface);
                transition: transform .14s cubic-bezier(.2,.8,.2,1); }
  .seltile .x:hover { transform: scale(1.16); }
  /* The rank is the handle: six dots and the number, dragged to change the order
     (the arrow keys do it too). The handle alone, so the rest of the tile still opens
     the deck, and on a phone still scrolls the conversation. */
  .seltile .rk.grip { pointer-events: auto; gap: 2px; height: 20px; padding: 0 6px 0 3px;
                      font-size: 10.5px; cursor: grab; touch-action: none; user-select: none;
                      -webkit-user-select: none; -webkit-tap-highlight-color: transparent; }
  .seltile .rk.grip:hover { background: rgba(0,0,0,.66); }
  .seltile .rk.grip:focus-visible { outline: 2px solid var(--primary); outline-offset: 2px; }
  .seltile .rk.grip svg { opacity: .9; }
  .selhint { margin: -3px 1px 9px; font-size: 12px; color: var(--muted); }
  .selblock.sorting { user-select: none; -webkit-user-select: none; }
  .selblock.sorting .seltile { transition: transform .15s ease; }
  .selblock.sorting .seltile.dragging { z-index: 3; transition: none; cursor: grabbing; }
  .selblock.sorting .seltile.dragging img { box-shadow: 0 12px 28px rgba(0,0,0,.32); }

  /* The block's round "clear the selection" button. */
  .ico { width: 28px; height: 28px; display: grid; place-items: center; border: 0;
         padding: 0; cursor: pointer; border-radius: 50%; background: var(--surface);
         color: var(--fg); box-shadow: 0 1px 4px rgba(0,0,0,.16);
         transition: transform .14s cubic-bezier(.2,.8,.2,1); }
  .ico:hover { transform: scale(1.12); }
  .ico.clear  { color: var(--act-pass); }

  /* The way out to Pexafy, in the slot "See N more" hands over: filled with the brand
     colour so it is seen. */
  .brandcta { display: inline-flex; align-items: center; gap: 10px; cursor: pointer;
              border: 0; background: var(--brand);
              padding: 9px 9px 9px 20px; border-radius: 999px; font-family: var(--font);
              font-size: 14px; font-weight: 600; color: #fff; letter-spacing: .1px;
              box-shadow: 0 8px 22px rgba(124,58,237,.32);
              transition: transform .14s cubic-bezier(.2,.8,.2,1), box-shadow .16s ease; }
  .brandcta:hover { transform: translateY(-1px); box-shadow: 0 12px 28px rgba(124,58,237,.44); }
  .brandcta:focus-visible { outline: 2px solid var(--primary); outline-offset: 3px; }
  .brandcta .arrow { display: grid; place-items: center; width: 28px; height: 28px;
                     border-radius: 50%; color: var(--primary); background: #fff;
                     transition: transform .2s cubic-bezier(.2,.8,.2,1); }
  .brandcta:hover .arrow { transform: translateX(3px); }

  /* ── Viewer: the deck, a page of the widget ──────────────────────────────
     Not a modal: it takes the grid's place and height (openViewer) and gives them
     back on close, so the host is never told the widget changed size and nothing in
     the conversation moves. Every colour comes from a --v-* token. */
  #viewer { position: relative; display: flex; overflow: hidden;
            animation: fade .18s ease; border-radius: 14px; color: var(--v-ink); }
  /* Full screen, on a phone only (goFull): the viewport's height (`dvh`, which
     follows the sliding address bar), no rounded corners, a painted ground. The
     notch and home-bar insets come from the host's `safeAreaInsets`:
     `env(safe-area-inset-*)` measures the iframe's own box and reports zero. */
  #viewer.vfull { border-radius: 0; width: 100%; height: 100vh; height: 100dvh;
                  background: var(--v-back);
                  padding: var(--safe-t, 0px) var(--safe-r, 0px) var(--safe-b, 0px) var(--safe-l, 0px); }
  @keyframes fade { from { opacity: 0; } to { opacity: 1; } }
  /* The blurred photo is a ground, pushed far out of focus so the eye lands on the
     card. */
  .vback { position: absolute; inset: 0; overflow: hidden; background: var(--v-back); }
  .vback i { position: absolute; inset: -18%; background-size: cover; background-position: center;
             filter: blur(64px) saturate(1.25) brightness(var(--v-blur-b)); transform: scale(1.2);
             opacity: 0; transition: opacity .38s ease; }
  .vback i.on { opacity: var(--v-blur-o); }
  .vback::after { content: ""; position: absolute; inset: 0;
                  background: radial-gradient(120% 90% at 50% 0%, var(--v-scrim), var(--v-scrim-2)); }
  .vshell { position: relative; z-index: 1; flex: 1; min-width: 0; display: flex;
            flex-direction: column; padding: 8px 12px 12px; gap: 7px; color: var(--v-ink); }
  /* The photo and its verbs are one block, centred in whatever height the host
     gives: the deck is cut to the photo's own box (sizeCards) and the buttons ride
     under it. */
  .vbody { flex: 1 1 auto; min-height: 0; display: flex; flex-direction: column;
           justify-content: center; gap: 10px; }

  /* The top bar holds only the position dashes; the deck carries no branding. */
  .vtop { display: flex; align-items: center; gap: 10px; flex: 0 0 auto; }
  /* One dash per photo: a position read at a glance. Each dash is a button (the bar
     is 3px, the target 15px) that jumps to its photo. */
  .vsegs { flex: 1; display: flex; align-items: center; gap: 3px; min-width: 0; }
  .vsegs button { flex: 1 1 0; min-width: 3px; display: block; padding: 6px 0;
                  border: 0; background: none; cursor: pointer; }
  .vsegs button:focus-visible { outline: 2px solid var(--act-sup); outline-offset: 1px;
                                border-radius: 3px; }
  .vsegs i { display: block; width: 100%; height: 3px; border-radius: 2px;
             background: var(--v-chip-hi);
             transition: background .2s ease, height .2s ease; }
  /* `:not(.now)`: the hover rule outranks `.vsegs i.now`, and would grey out the
     current dash under a resting mouse. */
  .vsegs button:hover i:not(.now) { background: var(--v-ink-2); }
  .vsegs i.done { background: var(--v-ink-3); }
  /* The dash on screen: taller, in the deck's own ink rather than a custom gradient. */
  .vsegs i.now { height: 5px; background: var(--v-ink); }

  .vdeck { position: relative; flex: 0 1 auto; min-height: 130px; touch-action: none;
           transition: height .28s cubic-bezier(.2,.8,.2,1); }
  .vcard { position: absolute; inset: 0; display: flex; align-items: center;
           justify-content: center; will-change: transform;
           transition: transform .32s cubic-bezier(.2,.8,.2,1), opacity .32s ease; }
  .vcard.drag { transition: none; }
  /* The card is the photo, in its own proportions: a portrait stays a portrait.
     sizeCards() writes the width and height in pixels, because CSS cannot fit a
     ratio into a box bounded on both axes; `data-ar` comes from the payload, and
     fixRatio() corrects it once the file loads if the payload had no dimensions. */
  .vcard .shot { position: relative; border-radius: 18px; overflow: hidden;
                 background: var(--v-chip); box-shadow: var(--v-card-shadow);
                 max-width: 100%; max-height: 100%; }
  .vcard img { width: 100%; height: 100%; object-fit: cover; display: block;
               user-select: none; -webkit-user-drag: none; }
  .vcard.top .shot { cursor: grab; }
  .vcard.top.drag .shot { cursor: grabbing; }
  /* The next card is drawn for real, in its final shape, and revealed as the top
     one leaves (revealNext). */
  .vcard.nx { opacity: 0; transform: scale(.94) translateY(11px);
              transition: opacity .22s ease, transform .3s cubic-bezier(.2,.8,.2,1); }
  .vcard.nx.up { opacity: 1; transform: none; }

  /* The verdict stamp: the verb's own glyph, on the card so it tilts and travels
     with the photo, in the corner the card is leaving (a mark on the edge it heads
     for is off screen too soon). A badge the size of the round verb, not a billboard
     over the photo. Its scale follows the drag inline (markTf), with no transition. */
  .vstamp { position: absolute; opacity: 0; pointer-events: none; z-index: 2;
            width: 14%; min-width: 48px; max-width: 76px;
            filter: drop-shadow(0 6px 16px rgba(0,0,0,.45)); }
  .vstamp svg { width: 100%; height: auto; display: block; }
  .vstamp.keep { left: 5%;  top: 5%; color: var(--act-keep);
                 transform: rotate(-18deg) scale(.66); }
  .vstamp.pass { right: 5%; top: 5%; color: var(--act-pass);
                 transform: rotate(18deg) scale(.66); }
  .vstamp.sup  { left: 50%; bottom: 24%; color: var(--act-sup);
                 transform: translateX(-50%) rotate(-8deg) scale(.66); }
  /* A button press throws the same stamp the gesture would: one verb, two ways. */
  @keyframes flashkeep {
    0%   { opacity: 0; transform: rotate(-18deg) scale(.55); }
    30%  { opacity: 1; transform: rotate(-18deg) scale(1.14); }
    68%  { opacity: 1; transform: rotate(-18deg) scale(1.04); }
    100% { opacity: 0; transform: rotate(-18deg) scale(1.1); }
  }
  @keyframes flashpass {
    0%   { opacity: 0; transform: rotate(18deg) scale(.55); }
    30%  { opacity: 1; transform: rotate(18deg) scale(1.14); }
    68%  { opacity: 1; transform: rotate(18deg) scale(1.04); }
    100% { opacity: 0; transform: rotate(18deg) scale(1.1); }
  }
  .vstamp.keep.flash { animation: flashkeep .5s cubic-bezier(.2,.8,.2,1); }
  .vstamp.pass.flash { animation: flashpass .5s cubic-bezier(.2,.8,.2,1); }

  /* On opening, the card sways once, damped, to show it can be thrown; the first
     real gesture stops it (stopCoaching). */
  @keyframes sway {
    0%   { transform: none; }
    14%  { transform: translateX(-19px) rotate(-2.6deg); }
    32%  { transform: translateX(16px)  rotate(2.2deg); }
    50%  { transform: translateX(-10px) rotate(-1.4deg); }
    66%  { transform: translateX(6px)   rotate(.8deg); }
    82%  { transform: translateX(-2px)  rotate(-.3deg); }
    100% { transform: none; }
  }
  /* Never while dragged: an animation outranks the inline transform the drag writes. */
  .vcard.top.sway:not(.drag) { animation: sway 1.9s cubic-bezier(.35,0,.25,1) .3s 1 both; }

  /* ≈ (more like this one): a blue burst over the deck (burstStar)… */
  .vburst { position: absolute; inset: 0; display: grid; place-items: center;
            pointer-events: none; z-index: 3; color: var(--act-sup);
            animation: burst .72s cubic-bezier(.2,.9,.25,1) forwards; }
  @keyframes burst {
    0%   { opacity: 0; transform: scale(.25); }
    28%  { opacity: 1; transform: scale(1.12); }
    54%  { opacity: 1; transform: scale(.98); }
    100% { opacity: 0; transform: scale(1.55); }
  }
  .vburst svg { filter: drop-shadow(0 0 26px rgba(18,184,232,.85)); }
  @keyframes ring {
    0%   { opacity: .85; transform: scale(.3); }
    100% { opacity: 0; transform: scale(2.3); }
  }
  .vburst i { position: absolute; width: 132px; height: 132px; border-radius: 50%;
              border: 3px solid var(--act-sup); animation: ring .72s ease-out forwards; }
  /* …and the badge it leaves on the photo it was asked from (markStar). */
  @keyframes pop { 0% { transform: scale(0); } 62% { transform: scale(1.24); }
                   100% { transform: scale(1); } }
  .vstar { position: absolute; top: 10px; right: 10px; width: 30px; height: 30px;
           display: grid; place-items: center; border-radius: 50%; color: #fff;
           background: var(--act-sup); box-shadow: 0 4px 14px rgba(0,0,0,.35);
           animation: pop .32s cubic-bezier(.2,.9,.25,1); }

  /* Credit and caption sit on the photo, under a scrim over their strip only, and
     travel with the card. White whatever the theme: the text is over a photograph. */
  .vinfo { position: absolute; left: 0; right: 0; bottom: 0; padding: 34px 14px 12px;
           color: #fff; pointer-events: none;
           background: linear-gradient(transparent, rgba(0,0,0,.30) 32%, rgba(0,0,0,.82)); }
  .vinfo .by { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap;
               font-size: 15px; font-weight: 700; line-height: 1.3;
               text-shadow: 0 1px 6px rgba(0,0,0,.5); }
  .vinfo .tag { padding: 1.5px 8px; border-radius: 999px; font-size: 11px;
                background: rgba(255,255,255,.2); backdrop-filter: blur(6px);
                -webkit-backdrop-filter: blur(6px); align-self: center;
                font-weight: 700; letter-spacing: .2px; color: #fff; }
  /* A narrow portrait card drops the licence chip rather than wrap it onto its own
     line; the licence is still on the photo page. */
  .shot.narrow .vinfo .opt { display: none; }
  .shot.narrow .vinfo { padding: 30px 12px 11px; }
  .vinfo .cap { margin: 3px 0 0; font-size: 12px; line-height: 1.45;
                color: rgba(255,255,255,.74); display: -webkit-box; -webkit-line-clamp: 2;
                -webkit-box-orient: vertical; overflow: hidden;
                text-shadow: 0 1px 5px rgba(0,0,0,.5); }

  /* The controls under the photo: the way back to the grid alone on the left, then
     five round verbs, centred — rewind, skip, ≈, like, and download. */
  .vctl { flex: 0 0 auto; display: grid; grid-template-columns: 1fr auto 1fr;
          align-items: center; padding: 2px 0; gap: 6px; }
  .vverbs { position: relative; display: flex; align-items: center;
            justify-content: center; gap: 9px; }
  /* The way back: a plate with a shadow, like the verbs, so it is found first. */
  .vside { display: grid; place-items: center; width: 40px; height: 40px; padding: 0;
           border: 0; border-radius: 50%; cursor: pointer;
           background: var(--v-btn); color: var(--v-ink);
           box-shadow: 0 3px 12px rgba(0,0,0,.22), 0 0 0 1px var(--v-btn-ring) inset;
           transition: background .16s ease, color .16s ease, box-shadow .16s ease,
                       transform .16s cubic-bezier(.2,.8,.2,1); }
  .vside svg { width: 20px; height: 20px; }
  .vside:hover { transform: translateY(-1px);
                 box-shadow: 0 7px 20px rgba(0,0,0,.3), 0 0 0 1px var(--v-btn-ring) inset; }
  .vside:active { transform: scale(.96); }
  .vside:disabled { opacity: .38; cursor: default; transform: none; }
  .vside.back { justify-self: start; }
  /* Opaque, raised plates with a coloured glyph: translucent chips washed out over a
     light photo. */
  .vbig { width: 50px; height: 50px; border-radius: 50%; display: grid; place-items: center;
          cursor: pointer; padding: 0; border: 0; background: var(--v-btn);
          color: var(--v-ink); box-shadow: 0 4px 14px rgba(0,0,0,.28),
                                           0 0 0 1px var(--v-btn-ring) inset;
          transition: transform .16s cubic-bezier(.2,.8,.2,1), box-shadow .16s ease,
                      opacity .16s ease; }
  .vbig:hover { transform: scale(1.09); box-shadow: 0 8px 22px rgba(0,0,0,.34),
                                                    0 0 0 1px var(--v-btn-ring) inset; }
  .vbig:active { transform: scale(.96); }
  .vbig:disabled { opacity: .38; cursor: default; transform: none; }
  .vbig:focus-visible { outline: 3px solid var(--act-sup); outline-offset: 2px; }
  .vbig.rewind { color: var(--act-rewind); }
  .vbig.pass   { color: var(--act-pass); }
  .vbig.keep   { color: var(--act-keep); }
  /* Filled when the photo is already liked, so the heart does not read as "like"
     when pressing it would unlike. */
  .vbig.keep.on { color: #fff; background: var(--act-keep);
                  box-shadow: 0 5px 18px color-mix(in srgb, var(--act-keep) 40%, transparent); }
  .vbig.get    { color: var(--act-get); }
  /* ≈ is the verb whose glyph is not read without help, so it takes the colour
     whole: white on blue, bigger, the blue carried into its shadow. Declared once:
     a second `.vbig.sup` colour rule would win and paint the glyph blue on blue. */
  .vbig.sup { width: 60px; height: 60px; background: var(--act-sup); color: #fff;
              box-shadow: 0 8px 22px color-mix(in srgb, var(--act-sup) 46%, transparent),
                          0 0 0 1px rgba(255,255,255,.3) inset; }
  .vbig.sup:hover { box-shadow: 0 12px 28px color-mix(in srgb, var(--act-sup) 58%, transparent),
                                0 0 0 1px rgba(255,255,255,.4) inset; }
  /* Spent: back to the row's own plate, or a dead button is the brightest thing here. */
  .vbig.sup:disabled { background: var(--v-btn); color: var(--v-ink);
                       box-shadow: 0 4px 14px rgba(0,0,0,.28),
                                   0 0 0 1px var(--v-btn-ring) inset; }
  @keyframes suppop { 40% { transform: scale(1.28); } 70% { transform: scale(.94); } }
  .vbig.sup.fire { animation: suppop .5s cubic-bezier(.2,.9,.25,1); }

  /* Compact heights: the picture wins, the chrome yields. */
  @media (max-height: 470px) {
    .vinfo .cap { display: none; }
    .vinfo { padding: 26px 12px 9px; }
    .vbig { width: 42px; height: 42px; }
    .vbig.sup { width: 50px; height: 50px; }
  }
  /* Narrow surfaces: five verbs and the way back still fit on a phone. */
  @media (max-width: 380px) {
    .vverbs { gap: 6px; }
    .vbig { width: 42px; height: 42px; }
    .vbig.sup { width: 50px; height: 50px; }
    .vside { width: 32px; height: 32px; }
  }

  /* ── Toast ─────────────────────────────────────────────────────────────── */
  /* Its own keyframes: `rise` animates `transform` without the translateX(-50%)
     that centres the toast, which made it slide in off-centre. */
  #toast { position: absolute; left: 50%; bottom: 14px; transform: translateX(-50%);
           z-index: 40; padding: 9px 16px; border-radius: 999px; font-size: 12.5px;
           font-weight: 600; color: #fff; background: rgba(20,20,26,.92);
           border: 1px solid rgba(255,255,255,.14); box-shadow: 0 10px 30px rgba(0,0,0,.4);
           backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px);
           animation: toastin .2s cubic-bezier(.2,.8,.2,1); pointer-events: none; }
  @keyframes toastin { from { opacity: 0; transform: translate(-50%, 8px); }
                       to   { opacity: 1; transform: translate(-50%, 0); } }
""".replace("__DARK__", _DARK_TOKENS)


# Widget logic. Reads the SDK from globalThis (inlined) or falls back to a CDN import.
# The reader's language: first in the frame's script, and a block of its own so
# the tests that run parts of the script in node can run it too.
_I18N_JS = """
/* ── The reader's language ────────────────────────────────────────────────
   Every text the reader sees is looked up here, in the site's languages
   (widget_i18n.py), by its English wording: a text with no translation is shown in
   English. The host says which language (MCP Apps `hostContext.locale`, ChatGPT's
   `window.openai.locale`), else the frame's own `navigator.language`. What the model
   reads — its context, the selection, the attributions in them — stays in English, and so
   do names: Pexafy, the photographers, the libraries. */
const I18N = (() => {
  const raw = __I18N__;
  const out = {};
  for (const lang of Object.keys(raw.t)) {
    const row = raw.t[lang], table = {};
    raw.k.forEach((key, i) => { table[key] = row[i]; });
    out[lang] = table;
  }
  return out;
})();
let LANG = "en";
function langOf(tag) {
  const l = String(tag || "").toLowerCase().replace(/_/g, "-");
  if (!l) return "";
  if (l === "en" || I18N[l]) return l;
  if (l.indexOf("zh") === 0) {
    const hant = /hant|-tw|-hk|-mo/.test(l);
    if (hant && I18N["zh-hant"]) return "zh-hant";
    return I18N["zh-hans"] ? "zh-hans" : (I18N.zh ? "zh" : "");
  }
  const base = l.split("-")[0];
  if (base === "en" || I18N[base]) return base;
  for (const k of Object.keys(I18N)) if (k.split("-")[0] === base) return k;
  return "";
}
function tx(src, vars) {
  const table = I18N[LANG];
  const own = table && table[src];
  let out = typeof own === "string" && own ? own : src;
  if (vars) out = out.replace(/\\{(\\w+)\\}/g, (m, k) => (k in vars ? String(vars[k]) : m));
  return elide(out);
}
/* French elides "de" before a vowel: "Photo d'Amanda Frank", not "Photo de Amanda".
   Only a name filled in can bring the vowel; the table's own texts are written elided. */
function elide(text) {
  if (LANG !== "fr") return text;
  return text.replace(/\\bde ([aeiouyàâäéèêëîïôöûùüAEIOUYÀÂÄÉÈÊËÎÏÔÖÛÙÜ])/g, "d'$1");
}
/* A text with a count. `one` and `other` are the English forms; a language with more
   (Polish, Russian, Arabic…) keeps them under the English `other`, by plural category,
   and one with no plural (Japanese…) a single string. */
function tn(n, one, other, vars) {
  const v = Object.assign({ n: fmtN(n) }, vars || {});
  const table = I18N[LANG];
  const own = table && table[other];
  let form = "";
  if (own && typeof own === "object") {
    let cat = "other";
    try { cat = new Intl.PluralRules(LANG).select(n); } catch (e) {}
    form = own[cat] || own.other || "";
  } else if (typeof own === "string") {
    form = own;
  }
  if (!form) form = n === 1 ? one : other;
  return form.replace(/\\{(\\w+)\\}/g, (m, k) => (k in v ? String(v[k]) : m));
}
function fmtN(n) {
  try { return new Intl.NumberFormat(LANG).format(n); } catch (e) { return String(n); }
}
/* "A, B or C" in the reader's language. */
function listOr(items) {
  try { return new Intl.ListFormat(LANG, { type: "disjunction" }).format(items); }
  catch (e) { return items.join(" / "); }
}
/* The language from what the host has said so far: true when it changed. */
function readLang() {
  let tag = "";
  try {
    const a = typeof app !== "undefined" ? app : null;
    const c = a && a.getHostContext && a.getHostContext();
    tag = (c && c.locale) || "";
  } catch (e) {}
  if (!tag) { try { tag = (window.openai && window.openai.locale) || ""; } catch (e) {} }
  if (!tag) { try { tag = navigator.language || ""; } catch (e) {} }
  const l = langOf(tag) || "en";
  if (l === LANG) return false;
  LANG = l;
  try { document.documentElement.lang = l; } catch (e) {}
  return true;
}
"""

_WIDGET_JS = _I18N_JS + """
const statusEl = document.getElementById("status");
const gridEl   = document.getElementById("grid");
const headEl   = document.getElementById("gridhead");
const brandEl  = headEl.querySelector(".brandmark");
const trailEl  = document.getElementById("trail");
const moreEl   = document.getElementById("gridmore");
const tipEl    = document.getElementById("gridtip");
const selEl    = document.getElementById("selblock");
const shapeWrap= document.getElementById("shapewrap");
const shapeBtn = document.getElementById("shapeb");
const shapeGl  = document.getElementById("shapeglyph");
const shapePop = document.getElementById("shapepop");
const acctWrap = document.getElementById("acctwrap");
const acctEl   = document.getElementById("acct");
const acctGlyph= document.getElementById("acctglyph");
const acctDot  = document.getElementById("acctdot");
const acctPop  = document.getElementById("acctpop");
const apTitle  = document.getElementById("apTitle");
const apDetail = document.getElementById("apDetail");
const wallEl   = document.getElementById("wall");
const lastEl   = document.getElementById("lastcall");
const viewerEl = document.getElementById("viewer");
const toastEl  = document.getElementById("toast");
const bugBtn   = document.getElementById("bugb");
const noteEl   = document.getElementById("sharenote");
const noteText = document.getElementById("sharenoteText");
const noteX    = document.getElementById("sharenoteX");

let app = null;                  // the SDK's App, once loaded (below)
readLang();

/* Whether anything was said on the status line since the page's own "loading". */
let statusSaid = false;
function say(msg, isErr) {
  statusSaid = true;
  statusEl.textContent = msg;
  statusEl.className = isErr ? "err" : "";
  statusEl.hidden = false;
  headEl.hidden = true;
  gridEl.hidden = true;
  moreEl.hidden = true;
  selEl.hidden = true;
  wallEl.hidden = true;
  lastEl.hidden = true;
}

// The inlined SDK, or the CDN when the bundle was not vendored (see _EXT_APPS_CDN).
let App, applyDocumentTheme;
const sdk = globalThis.__PEXAFY_EXTAPPS__;
if (sdk && sdk.App) {
  App = sdk.App; applyDocumentTheme = sdk.applyDocumentTheme;
} else {
  try {
    const mod = await import("__EXT_APPS_CDN__");
    App = mod.App; applyDocumentTheme = mod.applyDocumentTheme;
  } catch (e) {
    say(tx("Viewer SDK unavailable (CSP/network).") + " " + (e && e.message ? e.message : e), true);
    throw e;
  }
}

// autoResize: the host is told every height change (thumbnails load late and grow
// the grid).
app = new App({ name: "Pexafy", version: "__APP_VERSION__" }, undefined, { autoResize: true });

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"]/g,
    c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}
function cap(s) { s = String(s || ""); return s ? s.charAt(0).toUpperCase() + s.slice(1) : s; }

// Inline SVG icons: the sandbox blocks every external fetch. Filled, except the line
// glyphs further down (≈ and the shapes): a solid glyph holds its weight at 16px over
// a photograph, and lets the plate stay white while the glyph carries the colour.
const FILL = (d, size) => '<svg viewBox="0 0 24 24" width="' + (size || 24) + '" height="'
  + (size || 24) + '" fill="currentColor" aria-hidden="true"><path d="' + d + '"/></svg>';
const D = {
  close: "M18.3 5.71 12 12.01l-6.3-6.3-1.41 1.41 6.3 6.3-6.3 6.3 1.41 1.41 6.3-6.3 6.3 6.3 1.41-1.41-6.3-6.3 6.3-6.3z",
  heart: "M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35z",
  check: "M9 16.17 4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z",
  // A loop with an arrowhead: rewind steps back and takes back the swipe that left.
  rewind: "M13 4.07V1L8.45 5.55 13 10V6.09c2.84.48 5 2.94 5 5.91s-2.16 5.43-5 5.91v2.02c3.95-.49 7-3.85 7-7.93s-3.05-7.44-7-7.93zM7.11 8.53 5.7 7.11C4.8 8.27 4.24 9.61 4.07 11h2.02c.14-.87.49-1.72 1.02-2.47zM6.09 13H4.07c.17 1.39.72 2.73 1.62 3.89l1.41-1.42c-.52-.75-.87-1.59-1.01-2.47zm1.01 5.32c1.16.9 2.51 1.44 3.9 1.61V17.9c-.87-.15-1.71-.49-2.46-1.03L7.1 18.32z",
  down:  "M20 12l-1.41-1.41L13 16.17V4h-2v12.17l-5.58-5.59L4 12l8 8 8-8z",
  right: "M12 4l-1.41 1.41L16.17 11H4v2h12.17l-5.58 5.59L12 20l8-8z",
  // An arrow into a tray: opens the photo's page on Pexafy, where the file is.
  get:   "M5 20h14v-2H5v2zM19 9h-4V3H9v6H5l7 7 7-7z",
  // Back to the grid the deck was opened from.
  back:  "M20 11H7.8l5.6-5.6L12 4l-8 8 8 8 1.4-1.4L7.8 13H20v-2z",
  // ≈, "more like this one": read without a caption. Two waves, centred in the box.
  approx: "M4 9q4-4 8 0t8 0M4 15q4-4 8 0t8 0",
  // The account: a silhouette, recognised without a label in a 30px circle.
  user: "M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4z",
  // A bug: the contact form, opened on "Bug reports".
  bug: "M20 8h-2.81c-.45-.78-1.07-1.45-1.82-1.96L17 4.41 15.59 3l-2.17 2.17C12.96 5.06 12.49 5 12 5c-.49 0-.96.06-1.41.17L8.41 3 7 4.41l1.62 1.63C7.88 6.55 7.26 7.22 6.81 8H4v2h2.09c-.05.33-.09.66-.09 1v1H4v2h2v1c0 .34.04.67.09 1H4v2h2.81c1.04 1.79 2.97 3 5.19 3s4.15-1.21 5.19-3H20v-2h-2.09c.05-.33.09-.66.09-1v-1h2v-2h-2v-1c0-.34-.04-.67-.09-1H20V8zm-6 8h-4v-2h4v2zm0-4h-4v-2h4v2z",
  // Six dots, two columns of three: "drag me", as on the try-it page's list.
  grip: "M9 5a1.5 1.5 0 1 1-3 0 1.5 1.5 0 0 1 3 0zm0 7a1.5 1.5 0 1 1-3 0 1.5 1.5 0 0 1 3 0zm-1.5 8.5a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3zM18 5a1.5 1.5 0 1 1-3 0 1.5 1.5 0 0 1 3 0zm-1.5 8.5a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3zM18 19a1.5 1.5 0 1 1-3 0 1.5 1.5 0 0 1 3 0z",
  // The wall: an allowance is time-boxed and comes back; a padlock would say "forbidden".
  hourglass: "M6 2v6h.01L6 8.01 10 12l-4 4 .01.01H6V22h12v-5.99h-.01L18 16l-4-4 4-3.99-.01-.01H18V2H6zm10 14.5V20H8v-3.5l4-4 4 4zm-4-5l-4-4V4h8v3.5l-4 4z",
  // The trail's first chip: the search the assistant ran.
  anchor: "M17 15l1.55 1.55c-.96 1.69-3.33 3.04-5.55 3.37V11h3V9h-3V7.82C14.16 7.4 15 6.3 15 5c0-1.65-1.35-3-3-3S9 3.35 9 5c0 1.3.84 2.4 2 2.82V9H8v2h3v8.92c-2.22-.33-4.59-1.68-5.55-3.37L7 15l-4-3v3c0 3.88 4.92 7 9 7s9-3.12 9-7v-3l-4 3zM12 4c.55 0 1 .45 1 1s-.45 1-1 1-1-.45-1-1 .45-1 1-1z",
};
const ICON = {
  cross:   FILL(D.close, 24),
  heart:   FILL(D.heart, 24),
  heartS:  FILL(D.heart, 16),
  check:   FILL(D.check, 16),
  crossS:  FILL(D.close, 16),
  rewind:  FILL(D.rewind, 22),
  chooseS: FILL(D.check, 16),
  down:    FILL(D.down, 13),
  right:   FILL(D.right, 16),
  get:     FILL(D.get, 24),
  back:    FILL(D.back, 21),
  // The swipe stamps: the buttons' own glyphs, large.
  keepBig: FILL(D.heart, 62),
  passBig: FILL(D.close, 62),
};
/* The three formats, drawn as themselves: a rectangle in proportion reads as
   "portrait" in any language at 14px. Outlined (a filled one is a blob hiding the
   photo), all on one 24×24 grid so their strokes weigh the same. */
const SHAPE_BOX = {
  landscape: [2.5, 6.5, 19, 11],
  portrait:  [6.5, 2.5, 11, 19],
  square:    [4,   4,   16, 16],
};
// The menu's order, and the catalogue's: landscape first.
const SHAPES = ["landscape", "portrait", "square"];
const SHAPE_LABEL = { landscape: "Landscape", portrait: "Portrait", square: "Square" };
/* A shape's name for the reader. */
function shapeName(s) { return SHAPE_LABEL[s] ? tx(SHAPE_LABEL[s]) : s; }
// The filter's name on the tool surface (tooling.PUBLIC_ORIENTATION_PARAM): calls
// made from the frame go through the same tools as the model's.
const ORIENT_ARG = "__ARG_ORIENTATION__";
const shapeGlyph = (shape, size, w) => {
  const box = SHAPE_BOX[shape];
  if (!box) return "";
  return '<svg viewBox="0 0 24 24" width="' + size + '" height="' + size + '"'
    + ' fill="none" stroke="currentColor" stroke-width="' + (w || 2)
    + '" aria-hidden="true"><rect x="' + box[0] + '" y="' + box[1] + '" width="' + box[2]
    + '" height="' + box[3] + '" rx="2.6"/></svg>';
};
/* The filter is a set of shapes, which the API ORs. Kept in catalogue order, so the
   same ticks always make the same filter, and all three shapes normalise to []: no
   filter, and no call. */
function shapeList(v) {
  const list = Array.isArray(v) ? v : (v ? [v] : []);
  return SHAPES.filter(s => list.indexOf(s) !== -1);
}
function normShapes(v) {
  const list = shapeList(v);
  return list.length === SHAPES.length ? [] : list;
}
function sameShapes(a, b) { return normShapes(a).join() === normShapes(b).join(); }
/* "Landscape + Square", "Portrait": the shapes named, in their own order. */
function shapesLabel(list) { return shapeList(list).map(shapeName).join(" + "); }
/* The chosen shapes drawn together on the one grid: two nest (landscape over
   portrait reads as a cross, "either"), and one alone is the tile's own glyph. */
function shapesGlyph(list, size, w) {
  const on = shapeList(list);
  if (on.length < 2) return shapeGlyph(on[0] || "landscape", size, w);
  let rects = "";
  for (const s of on) {
    const b = SHAPE_BOX[s];
    rects += '<rect x="' + b[0] + '" y="' + b[1] + '" width="' + b[2] + '" height="' + b[3]
      + '" rx="2.6"/>';
  }
  return '<svg viewBox="0 0 24 24" width="' + size + '" height="' + size + '" fill="none"'
    + ' stroke="currentColor" stroke-width="' + (w || 2) + '" aria-hidden="true">' + rects
    + "</svg>";
}

// ≈ is drawn with lines: two waves filled at small sizes close up into two blobs.
// `star` is the ≈ glyph; the name is older than the glyph.
const STROKE = (d, size, w) =>
  '<svg viewBox="0 0 24 24" width="' + size + '" height="' + size + '" fill="none"'
  + ' stroke="currentColor" stroke-width="' + w + '" stroke-linecap="round"'
  + ' aria-hidden="true"><path d="' + d + '"/></svg>';
const star = (size) => STROKE(D.approx, size, size > 40 ? 2 : 2.7);
ICON.star = star(30);
ICON.supBig = star(62);
ICON.starS = star(14);
/* The shape filter's mark when nothing is filtered: the three formats nested. */
ICON.everyShape =
  '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor"'
  + ' stroke-width="1.8" aria-hidden="true">'
  + '<rect x="2" y="7" width="20" height="10" rx="2.4" opacity=".45"/>'
  + '<rect x="7" y="4" width="10" height="16" rx="2.4" opacity=".45"/>'
  + '<rect x="5.5" y="5.5" width="13" height="13" rx="2.4"/></svg>';
// A heart small enough to sit inside the coach's sentence.
ICON.heartTip = FILL(D.heart, 12);
// The heart on the liked photos' title.
ICON.heartSel = FILL(D.heart, 14);
// A tick for the shape menu's boxes and the coach's done steps.
ICON.tickS = FILL(D.check, 11);
ICON.anchor = FILL(D.anchor, 15);
ICON.user = FILL(D.user, 17);
ICON.bug = FILL(D.bug, 16);
ICON.grip = FILL(D.grip, 12);
ICON.crossXS = FILL(D.close, 12);
ICON.hourglass = FILL(D.hourglass, 20);

/* Which Pexafy this is: the origin of the server's `cta.url` (its PEXAFY_WEB_URL, on
   the result's `_meta`), adopted by buildPhotos. Every link then goes to the site that
   server belongs to, preprod included, and all of them are treated alike by a host
   that opens links in the app's declared domain without asking (ChatGPT). */
let PEXAFY_HOME = "https://pexafy.com";
let PEXAFY_PHOTO_BASE = PEXAFY_HOME + "/photos/";
function adoptSite(url) {
  try {
    const origin = new URL(url).origin;
    if (origin && /^https?:/.test(origin)) {
      PEXAFY_HOME = origin;
      PEXAFY_PHOTO_BASE = origin + "/photos/";
    }
  } catch (e) {}
}
// The server's link to this answer on the site and its label (`cta`), for the wordmark,
// the "Open in Pexafy" button and the download fallback.
let footUrl = PEXAFY_HOME, footLabel = "Open in Pexafy";

function hostCan(name) {
  try {
    const c = app.getHostCapabilities && app.getHostCapabilities();
    return !!(c && c[name]);
  } catch (e) { return false; }
}
/* ── Speaking in the conversation ─────────────────────────────────────────
   A user turn in the thread. ChatGPT renders `ui/message` as an assistant message
   and acts on nothing, so its own `window.openai.sendFollowUpMessage` (a user turn it
   runs) is tried first. Returns true when something took the text. */
function speak(text) {
  const oai = window.openai;
  if (oai && typeof oai.sendFollowUpMessage === "function") {
    try { oai.sendFollowUpMessage({ prompt: text }); return true; } catch (e) {}
  }
  if (hostCan("message")) {
    try {
      app.sendMessage({ role: "user", content: [{ type: "text", text: text }] });
      return true;
    } catch (e) {}
  }
  return false;
}

/* `scrollIntoView` from inside the iframe scrolls the host's page too: the one way
   the widget can move the reader. */
function reveal(el, block) {
  try {
    if (el && el.scrollIntoView) el.scrollIntoView({ block: block || "center", behavior: "smooth" });
  } catch (e) {}
}

/* A spoken turn lands below the widget: go to the widget's end at once, and let the
   host's own scroll to the new turn have the last word. */
function followTheAnswer() { reveal(document.getElementById("end"), "end"); }

/* ── Which surface this is, said in every link ────────────────────────────
   The grid renders in any MCP Apps host, and a click from inside an iframe carries
   no useful referrer. So every link to Pexafy, and every page URL handed to the
   conversation, carries utm_* (parsed by the traffic pipeline): utm_medium=mcp_grid,
   utm_source=the host, utm_campaign=the control pressed. The host names itself in
   the handshake (`hostInfo.name`, then the context's `userAgent`); ChatGPT is
   recognised by `window.openai`. Resolved lazily: none of it exists before connect(). */
let surfaceName = null;
function slug(v) {
  return String(v || "").toLowerCase().replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "").slice(0, 24);
}
function surface() {
  if (surfaceName) return surfaceName;
  let n = "";
  try { const i = app.getHostVersion && app.getHostVersion(); n = slug(i && i.name); } catch (e) {}
  if (!n) { try { const c = app.getHostContext && app.getHostContext(); n = slug(c && c.userAgent); } catch (e) {} }
  if (!n && window.openai) n = "chatgpt";
  surfaceName = n || "mcp-host";
  return surfaceName;
}
/* Only ever our own pages. A preview URL is HMAC-signed, and a provider's page is not
   ours to tag. */
function tagged(url, what) {
  if (!url) return url;
  try {
    const u = new URL(url, PEXAFY_HOME);
    if (u.origin !== PEXAFY_HOME) return url;
    u.searchParams.set("utm_source", surface());
    u.searchParams.set("utm_medium", "mcp_grid");
    u.searchParams.set("utm_campaign", what || "grid");
    return u.toString();
  } catch (e) { return url; }
}

function toast(msg, show) {
  toastEl.textContent = msg;
  toastEl.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { toastEl.hidden = true; }, 2600);
  // The toast sits at the foot of a tall widget: an instruction (as opposed to a
  // receipt) is scrolled into view.
  if (show) reveal(toastEl, "end");
}
function openLink(url, what) {
  if (!url) return;
  try { app.openLink({ url: tagged(url, what) }); } catch (e) {}
}

/* The image links of each result, keyed by photo_id, from the result's `_meta`: the
   host hands `_meta` to the widget and not to the model, which cannot open a link.
   A running map, so the pages further up the trail keep their pictures. The server
   also leaves the links in `structuredContent` while previews.PREVIEW_CHANNEL is
   "both"; fields() falls back to those. */
const LINKS = Object.create(null);
/* A result's `_meta` when it is this server's: at least one `pexafy/` key. One that is
   empty, or holds the host's own keys alone, says nothing about the answer and counts
   as absent (null). */
function ourMeta(meta) {
  if (!meta || typeof meta !== "object") return null;
  for (const k of Object.keys(meta)) if (k.indexOf("pexafy/") === 0) return meta;
  return null;
}
function learnLinks(meta) {
  /* The link to this answer on the site (origin.py), read by
     buildPhotos. First, and per answer: one that carries none has none, rather than
     the last one's. */
  const cta = meta && meta["pexafy/cta"];
  ctaMeta = (cta && typeof cta === "object" && typeof cta.url === "string")
    ? { label: String(cta.label || ""), url: cta.url } : null;
  /* The account corner (budget.py): who is asking, and the wording and button of the
     allowance panel, whose numbers stay in `sc.budget`. Per answer, like the link. An
     answer that came with no `_meta` of ours (ourMeta: none at all, an empty one, the
     host's keys alone) keeps who was asking, which one answer does not change, and
     loses the wording, which was the previous answer's. */
  /* The coach, which plays once per person (coach.py): whether this reader has met it,
     and where to say they just did. Per answer; an answer with no `_meta` of ours keeps
     what the last one said. */
  const hm = meta && meta["pexafy/host"];
  if (hm && typeof hm === "object" && hm.assistant) hostMeta = { assistant: String(hm.assistant) };
  const cm = meta && meta["pexafy/coach"];
  if (cm && typeof cm === "object") {
    coachMeta = { show: cm.show !== false,
                  url: typeof cm.post_url === "string" ? cm.post_url : "",
                  token: typeof cm.token === "string" ? cm.token : "" };
  } else if (ourMeta(meta)) {
    coachMeta = null;
  }
  if (ourMeta(meta)) {
    const acct = meta["pexafy/account"];
    const a = (acct && typeof acct === "object") ? acct : {};
    acctMeta = {
      account: (a.account && typeof a.account === "object") ? a.account : null,
      budget: (a.budget && typeof a.budget === "object") ? a.budget : null,
    };
  } else if (acctMeta) {
    acctMeta = { account: acctMeta.account, budget: null };
  }
  const links = meta && meta["pexafy/previews"];
  if (links && typeof links === "object") {
    for (const id of Object.keys(links)) {
      const v = links[id];
      if (v && typeof v === "object") LINKS[id] = v;
    }
  }
  /* The question behind this answer (origin.py), so the shape filter can ask it
     again. Absent for a by-image search from an uploaded file: no filter then. */
  const org = meta && meta["pexafy/origin"];
  if (org && typeof org === "object" && org.tool && org.arguments) {
    ORIGIN = {
      tool: String(org.tool),
      args: Object.assign({}, org.arguments),
      shape: normShapes(org.orientation),
    };
  }
  /* Permission to post the reader's likes to the server (selection.py), whose
     selection tool hands them to the model on request: a signed, expiring token
     naming this caller. The frame holds no credential of its own. */
  const cap = meta && meta["pexafy/selection"];
  if (cap && typeof cap === "object" && cap.token && cap.post_url) {
    // `tool` names the selection tool. Without it the model context carries the
    // whole selection (writeContextToModel).
    selCap = { token: String(cap.token), url: String(cap.post_url),
               tool: cap.tool ? String(cap.tool) : "",
               // Where the view is kept in a host that keeps none (serverViewPut).
               view: cap.view_url ? String(cap.view_url) : "" };
  }
}
let selCap = null;
let ctaMeta = null;
let acctMeta = null;     // { account, budget } from `_meta["pexafy/account"]`, or null
let coachMeta = null;    // { show, url, token } from `_meta["pexafy/coach"]`, or null
/* How to re-ask the question of the page on screen: {tool, args, shape}. Set by
   learnLinks from every tool result and every search the widget runs; each trail
   page keeps its own copy, which showStep puts back. */
let ORIGIN = null;
/* The words the page on screen was searched with, or "": its origin's
   `__ARG_SENTENCE__`, which every refinement echoes. */
function wordsOf(page) {
  const o = page && page.origin;
  const q = o && o.args && o.args.__ARG_SENTENCE__;
  return (typeof q === "string" && q.trim()) ? q.trim() : "";
}
const FRAME_ID = Math.random().toString(36).slice(2) + "-" + Date.now().toString(36);

function fields(photo) {
  // Every pixel comes from Pexafy's signed CDN, the one image origin the resource CSP
  // allows: provider URLs would be blocked and paint broken images, so they are
  // never used, not even as a fallback.
  const link = LINKS[photo.photo_id || ""] || {};
  const thumb = link.small || thumbUrl(photo.preview_url);
  let credit = "";
  const a = photo.attribution;
  if (a && typeof a === "object") credit = a.plain || "";
  else if (typeof a === "string") credit = a;
  // The plain credit ends with "(<licence URL>)", left out of the line shown.
  const mUrl = credit.match(/\\((https?:\\/\\/[^)\\s]+)\\)/);
  if (mUrl) credit = credit.replace(mUrl[0], "").trim();
  const author = photo.photographer_full_name || "";
  const w = photo.width, h = photo.height;
  const pid = photo.photo_id || "";
  return {
    thumb,
    // The deck shows one photo at a time and gets the sharper file; the grid keeps 480w.
    large: link.large || thumbUrl(photo.preview_url_large) || thumb,
    credit, author,
    id: pid,
    // Numbers, so a card has its final shape before its file loads.
    w: w || 0, h: h || 0,
    source: photo.source || "",
    license: photo.license_type || "",
    orientation: photo.orientation || "",
    // Pexafy's own one-line caption, shown under the credit in the deck.
    description: photo.alt_description || "",
    pexafyUrl: pid ? (PEXAFY_PHOTO_BASE + encodeURIComponent(pid)) : "",
  };
}

/* The previews arrive whole in `_meta`. Their copy in the half the model reads comes
   as a path under the thumbnails' base (previews._previews_without_address), so that
   the model has no picture to show again under the grid: the base goes back here. */
const THUMB_BASE = "__THUMB_BASE__";
function thumbUrl(link) {
  const s = typeof link === "string" ? link : "";
  if (!s || s.startsWith("https://") || s.startsWith("http://")) return s;
  return THUMB_BASE ? THUMB_BASE + "/" + s : "";
}

/* "Photo by <author> on <source>", and "Photo on <source>" without a name: never a
   placeholder word standing in for one. */
function creditText(f) {
  if (!f.author && !f.source) return f.credit || "";
  return "Photo" + (f.author ? " by " + f.author : "") + (f.source ? " on " + f.source : "");
}

/* The same credit as the reader reads it: in their language. creditText stays the
   one the model is given. */
function shownCredit(f) {
  if (!f.author && !f.source) return f.credit || "";
  if (f.author && f.source) return tx("Photo by {author} on {source}", { author: f.author, source: f.source });
  if (f.author) return tx("Photo by {author}", { author: f.author });
  return tx("Photo on {source}", { source: f.source });
}

// Plain text, not a link: the deck's one way out is the download button.
function creditHtml(f) {
  return esc(shownCredit(f));
}

/* ── What the reader likes ────────────────────────────────────────────────
   Every change reaches the assistant without a turn in the thread: the model context
   (writeContextToModel), the host's store, and the server (publishSelection), which
   the selection tool reads. */
let PHOTOS = [];
/* By photo_id, not by position: a refinement replaces the photos in those
   positions. `keptPhotos` holds the photo itself, so a like from an earlier page
   survives the page. */
const kept = new Set();          // photo_id
const keptPhotos = new Map();    // photo_id -> fields()

function keptList() { return Array.from(keptPhotos.values()); }
function isKept(i) { const f = DECK[i]; return !!(f && kept.has(f.id)); }
/* The tile showing this photo in the grid on screen, if any. */
function cardOf(id) {
  for (const c of gridEl.children) if (c.dataset.pid === id) return c;
  return null;
}
/* A photo as someone about to use it needs it: its page, the image, the credit to
   print (and its two halves), then the photo_id the tools take. */
function photoLines(f, indent) {
  const p = indent || "";
  const out = [];
  if (f.pexafyUrl) out.push(p + tagged(f.pexafyUrl, "chat"));
  // Not when the list stands in for a tool that could not be reached: the grid is on
  // screen, and the selection tool hands over no image link where it is
  // (selection.get_selected_photos) — a model can show one again in the chat.
  if (f.large && !selPostFailed) out.push(p + "Image: " + f.large);
  const credit = creditText(f);
  if (credit) out.push(p + "Attribution: " + credit);
  if (f.author) out.push(p + "Photographer: " + f.author);
  if (f.source) out.push(p + "Source: " + f.source);
  out.push(p + 'photo_id: "' + f.id + '"');
  return out.join("\\n");
}

/* The liked photos as a numbered list, #1 the first liked: the numbers the reader and
   the assistant use afterwards. Written into the model context only when there is no
   selection tool. */
function selectionText() {
  const list = keptList();
  if (!list.length) return "";
  return list.map((f, i) => "#" + (i + 1) + "\\n" + photoLines(f, "  ")).join("\\n");
}

/* One push per burst of changes: a host that serves a context read a moment ago
   (ChatGPT, openai/openai-apps-sdk-examples#221) would otherwise answer about an
   intermediate state, "one photo" for two. `ctxRevision` numbers the pushes; it goes
   to the server with the selection and into `privateContent`, never to the model. */
let ctxTimer = null;
let ctxHold = 0;              // >0 while a batch is being applied; nothing is published
let ctxRevision = 0;

function pushContextToModel() {
  if (ctxHold) return;
  if (ctxTimer) { clearTimeout(ctxTimer); ctxTimer = null; }
  ctxTimer = setTimeout(() => { ctxTimer = null; writeContextToModel(); }, 90);
}

/* A batch of changes is one state: `fn` runs with publishing held, then one push. */
function withHeldContext(fn) {
  ctxHold++;
  try { fn(); } finally { ctxHold--; }
  pushContextToModel();
}

/* What the server is told: everything a question about a liked photo can ask for,
   more than the model context carries. The tool hands it over when asked. */
function selectionRecords() {
  return keptList().map((f, i) => ({
    rank: i + 1,
    photo_id: f.id,
    description: f.description || "",
    photographer: f.author || "",
    source: f.source || "",
    license: f.license || "",
    width: f.w || 0,
    height: f.h || 0,
    orientation: f.orientation || "",
    url: f.pexafyUrl ? tagged(f.pexafyUrl, "chat") : "",
    image_url: f.large || "",
    attribution: creditText(f),
  }));
}

/* Posted as text/plain: a JSON content type needs a CORS preflight, which the host's
   sandbox may leave unanswered. No credential travels; the token is the permission. */
/* Whether this frame has anything to tell the server: a like or an unlike, its own or
   one put back from a view. A fresh frame has not — and in a host that mounts the grid
   again at every turn (VS Code), its empty first word replaced the selection the reader
   had just made, a moment before the model asked for it. */
let selSpoken = false;

/* Whether a post has failed in this frame. The server then does not hold what the
   reader likes, and the selection tool would answer "nothing liked" to a reader who
   liked three: so the model context carries the selection itself, as it does when the
   server offers no tool (writeContextToModel), and stops naming the tool. Seen in
   ChatGPT, 2026-10-03: a developer connector keeps the content security policy it was
   created with, a published app the reviewed one until its updated definition passes
   review; the browser refused every post, and the tool read 0 for two photos liked.
   Failed once, failed for the frame: a later post that gets through only hands the
   tool the list the context already carries, and a context that changes its mind
   mid-conversation is the confusing one. */
let selPostFailed = false;

function selectionPostFailed(why) {
  if (selPostFailed) return;
  selPostFailed = true;
  console.warn("Pexafy: the liked photos did not reach the server (" + why
               + "); the model context carries them instead.");
  pushContextToModel();
}

function publishSelection() {
  if (!selCap || !selSpoken) return;
  const body = JSON.stringify({
    token: selCap.token,
    // Which frame is counting: `revision` orders this frame's writes only, and a
    // reloaded frame starts again at one.
    frame: FRAME_ID,
    revision: ctxRevision,
    items: selectionRecords(),
  });
  let sent;
  try {
    sent = fetch(selCap.url, { method: "POST", body: body, mode: "cors",
                               credentials: "omit", keepalive: true,
                               headers: { "Content-Type": "text/plain;charset=UTF-8" } });
  } catch (e) {
    selectionPostFailed(String((e && e.message) || e));
    return;
  }
  // Refused by the page's policy or by the network, or answered with an error (a token
  // refused, a list too large): either way the server does not hold the selection.
  Promise.resolve(sent).then(
    (r) => { if (r && r.ok === false) selectionPostFailed("HTTP " + r.status); },
    (e) => selectionPostFailed(String((e && e.message) || e)));
}

function writeContextToModel() {
  /* Short, because it is read on every turn, and state only, never an instruction: it
     says what only the widget knows — which grid is on screen, that its tiles carry no
     numbers, and how many photos the reader liked, with the tool that returns them.
     Without that tool — switched off on the server, or holding nothing because this
     frame could not post to it (selPostFailed) — the selection itself goes in, as a
     numbered list. */
  const s = trail[step] || {};
  const list = keptList();
  const tool = selCap && !selPostFailed && selCap.tool;
  const where = s.ref
    ? 'Pexafy grid on screen: photos similar to photo_id "' + s.ref.id
      + '", requested by the reader from inside the grid.'
    : "Pexafy grid on screen: search results.";
  const numbers = " Its tiles carry no numbers; photos are identified by photo_id.";
  const count = list.length;
  const picks = count
    ? " Liked by the reader: " + count
      + (count > 1 ? " photos, numbered #1 to #" + count : " photo, numbered #1")
      + (selReordered ? " on screen in the order the reader arranged them by dragging, not the order liked"
                      : " on screen in the order liked")
      + (tool ? "; " + tool + " returns them with their details." : ":")
    : " Liked by the reader: none yet"
      + (tool ? "; " + tool + " returns them once there are some." : ".");
  const text = where + numbers + picks
    + (tool || !list.length ? "" : "\\n\\n" + selectionText());
  ctxRevision++;
  publishSelection();
  rememberForModel(text, {
    selection_count: list.length,
    selection_revision: ctxRevision,
    selected_photo_ids: list.map(f => f.id),
  });
  if (!hostCan("updateModelContext")) return;
  try {
    app.updateModelContext({
      content: [{ type: "text", text: text }],
      // One fact per key; anything more about the liked photos is a tool call away.
      // `grid_photo_ids` is what is on screen, which the model cannot see.
      structuredContent: {
        selection_count: list.length,
        selected_photo_ids: list.map(f => f.id),
        selection_tool: tool || null,
        grid_photo_ids: PHOTOS.slice(0, shown).map(f => f.id),
        refined_from_photo_id: s.ref ? s.ref.id : null,
      },
    });
  } catch (e) {}
}

/* ── The host's store: three documented keys ─────────────────────────────
   ChatGPT's `setWidgetState` takes `modelContent` (shown to the model),
   `privateContent` (not shown) and `imageIds`; an unknown top-level key is
   undocumented and may be exposed whole. So the view state (trail, selection token)
   lives inside `privateContent`, under UI_KEY. The store is also a second channel for
   the model context, since `ui/update-model-context` can serve the model a stale one
   on ChatGPT (openai/openai-apps-sdk-examples#221).

   The call replaces the store whole, so each write is composed from the parts its
   callers own (the model's text and selection, the view); a part this frame has not
   written yet is carried over from what the host holds. One coalesced queue; `now`
   is for a caller that cannot wait. */
const UI_KEY = "pexafyView";

function hostState() {
  try { const oai = window.openai; return (oai && oai.widgetState) || null; }
  catch (e) { return null; }
}

/* The view in a host state: inside `privateContent`, or at the top level, where the
   previous version of this widget wrote it (a conversation open across a deploy). */
function viewIn(st) {
  if (!st || typeof st !== "object") return null;
  const priv = st.privateContent;
  const v = (priv && typeof priv === "object" && priv[UI_KEY]) || st[UI_KEY];
  return (v && typeof v === "object") ? v : null;
}

/* What the host held when this document started, read once at module scope: the
   first render saves (capShown) before restoreUi runs, and would otherwise overwrite
   the very state it is about to restore. */
const BOOT_STATE = hostState();

let storeParts = null;       // { text, sel, view }, seeded from the host on the first write
let storeTimer = null;

/* A host state split into the parts the writers own. */
function partsOf(st) {
  const s = (st && typeof st === "object") ? st : {};
  const priv = (s.privateContent && typeof s.privateContent === "object") ? s.privateContent : null;
  const sel = Object.assign({}, priv);
  delete sel[UI_KEY];                    // the view is a part of its own
  return { text: s.modelContent == null ? null : s.modelContent, sel: sel, view: viewIn(s) };
}

function storeWrite() {
  const oai = window.openai;
  if (!oai || typeof oai.setWidgetState !== "function" || !storeParts) return;
  const priv = Object.assign({}, storeParts.sel);
  if (storeParts.view) priv[UI_KEY] = storeParts.view;
  try {
    oai.setWidgetState({
      modelContent: storeParts.text == null ? "" : storeParts.text,
      privateContent: priv,
      imageIds: [],
    });
  } catch (e) {}
}

function storePut(patch, now) {
  const oai = window.openai;
  if (!oai || typeof oai.setWidgetState !== "function") return;
  if (!storeParts) storeParts = partsOf(hostState());
  Object.assign(storeParts, patch);
  if (storeTimer) { clearTimeout(storeTimer); storeTimer = null; }
  if (now) { storeWrite(); return; }
  // Long enough to fold a burst of likes or swipes into one write, short enough to
  // stay a beat behind at most.
  storeTimer = setTimeout(() => { storeTimer = null; storeWrite(); }, 300);
}

function rememberForModel(text, sel) {
  storePut({ text: text, sel: sel });
}

/* Who else keeps the view: the server, in a host with no store (serverViewPut). */
let viewSink = null;

function rememberView(view, now) {
  storePut({ view: view }, now);
  if (viewSink) viewSink(view, now);
}

/* ── Surviving a reload of the frame ──────────────────────────────────────
   A host may move the iframe in its DOM (to promote it to full screen, say), and
   moving an iframe reloads it: deck, likes and trail are gone. Nothing inside the
   frame survives that (the sandbox's opaque origin makes sessionStorage and
   localStorage throw), but the host's store does. So the view writes what it needs
   to find its place again, by photo_id rather than position, and reads it back once,
   with the first result. A view older than fifteen minutes is not restored. */
const UI_TTL_MS = 15 * 60 * 1000;
let uiRestored = false;      // read once per frame, on the first result

/* A ceiling for the trail in the host's store: past it the oldest refinements are
   dropped. The anchor's slot stays, since steps are indexes into the trail. */
const TRAIL_BUDGET = 192 * 1024;

/* The anchor comes back with the host's next tool result, so its photos are stored
   only when the reader filtered it here: the one case restoreUi reads them. What its
   `_meta` said always is — its link and the allowance panel's wording, with the
   payload they belong to — since a reloaded frame may not be handed that `_meta` again
   and the view is then the only copy (takeBack). */
function packAnchor(pg) {
  const cta = pg.baseCta || pg.cta || null;
  if (!normShapes(pg.shape).length) {
    const stub = { shape: [], cta: cta, psig: (pg.psigs && pg.psigs[0]) || "" };
    if (pg.wording) stub.wording = pg.wording;
    return stub;
  }
  return { photos: pg.photos, cta: pg.cta, baseCta: cta, wording: pg.wording || null,
           shape: shapeList(pg.shape), origin: pg.origin || null, psigs: pg.psigs || [] };
}

/* One page, compact: `base` is dropped when it is `photos` (every unfiltered page)
   and rebuilt by adoptPage. */
function packPage(pg) {
  const out = {
    ref: pg.ref, photos: pg.photos, cta: pg.cta, baseCta: pg.baseCta,
    shape: shapeList(pg.shape), origin: pg.origin || null, psigs: pg.psigs || [],
  };
  if (pg.base && pg.base !== pg.photos) out.base = pg.base;
  return out;
}

/* …and back, every field defaulted: render() reads `psigs` on every tool result. */
function adoptPage(pg) {
  pg.photos = pg.photos || [];
  pg.base = pg.base || pg.photos;
  pg.baseCta = pg.baseCta || pg.cta || null;
  pg.shape = normShapes(pg.shape);
  pg.origin = pg.origin || null;
  pg.psigs = Array.isArray(pg.psigs) ? pg.psigs : [];
  return pg;
}

function trimTrail(budget) {
  const cap = budget || TRAIL_BUDGET;
  let out = trail.map((pg, i) => (i ? packPage(pg) : packAnchor(pg)));
  while (out.length > 1) {
    let size;
    try { size = JSON.stringify(out).length; } catch (e) { return out.slice(0, 1); }
    if (size <= cap) break;
    out = [out[0]].concat(out.slice(2));      // the oldest refinement goes first
  }
  return out;
}

/* Which answer a view was written over: the first photo and the count of the
   anchor's `base`, the answer the host sent. Not the photos on screen: the shape
   filter changes those, and the host sends the unfiltered answer again on reload. */
function resultSig() {
  const anchor = trail[0];
  const photos = (anchor && (anchor.base || anchor.photos)) || [];
  return ((photos[0] && photos[0].id) || "") + ":" + photos.length;
}

/* The view state, through the store's queue. `now` writes at once, for the callers
   that cannot wait: what follows may reload the frame (opening the deck asks for
   full screen, closing it gives the screen back), or the state cost a tool call (a
   shape filter, which takes seconds). */
function saveUi(now) {
  const view = {
    cap: selCap,                 // see learnLinks: a reloaded frame gets no `_meta`
    who: acctWho,                // …nor the account corner (takeBack)
    coach: coachState(),
    deck: deckOpen,
    pid: (deckOpen && DECK[idx]) ? DECK[idx].id : null,
    kept: keptList().map(f => f.id),
    shown: shown,
    note: noteClosed,
    reordered: selReordered,
    // Refined and filtered pages exist only in this frame: the widget made those
    // calls itself, and the host holds only the original result.
    trail: trimTrail(),
    step: step,
    // A later search in the conversation paints other photos: a view is restored
    // only over the answer it was written for.
    sig: resultSig(),
    at: Date.now(),
  };
  // The wall on screen, if it is one: it has no anchor to keep its wording (paintWall).
  if (wallView) view.wall = wallView;
  rememberView(view, now);
}

/* What a frame reloaded without its answer's `_meta` takes back from its view, before
   it builds anything. The answer's own link and panel wording, when the view was written
   over this very answer — known by its photographs, as render() knows an answer it
   already has, or for a wall by its notice (wallSig). The site the server belongs to,
   on which every link and photo page is built, and who is asking, whatever the answer:
   they belong to the conversation. The view's age is not checked (restoreUi checks it
   for the trail and the deck): none of this goes stale. What the answer brought itself
   always wins. Once per frame. */
let tookBack = false;
function takeBack(sc) {
  if (tookBack) return;
  tookBack = true;
  const ui = viewIn(BOOT_STATE);
  if (!ui) return;
  const anchor = (Array.isArray(ui.trail) && ui.trail[0]) || {};
  const psig = anchor.psig || (Array.isArray(anchor.psigs) && anchor.psigs[0]) || "";
  const same = !!psig && psig === payloadSig(sc);
  const cta = anchor.baseCta || anchor.cta;
  if (!ctaMeta && !(sc && sc.cta) && cta && typeof cta.url === "string") {
    // buildPhotos adopts the site from it; over another answer, the site alone.
    if (same) ctaMeta = { label: String(cta.label || ""), url: cta.url };
    else adoptSite(cta.url);
  }
  // The account corner, only for an answer that brought none: no `_meta` of ours —
  // none, an empty one, the host's keys alone (learnLinks then leaves `acctMeta`
  // unset) — nor an earlier server's fields in `sc`. A `_meta` of ours that names no
  // account did say something: nobody is known, and a live frame drew no corner.
  if (acctMeta || (sc && (sc.account || sc.cta))) return;
  const who = (ui.who && typeof ui.who === "object") ? ui.who : null;
  // The wording, over the very answer it came with: the anchor's, known by its photos,
  // or the wall's, known by its signature (paintWall).
  const wall = (ui.wall && typeof ui.wall === "object") ? ui.wall : null;
  const wsig = wallSig(sc);
  let wording = null;
  if (same && anchor.wording && typeof anchor.wording === "object") wording = anchor.wording;
  else if (wsig && wall && wall.sig === wsig && wall.wording && typeof wall.wording === "object") {
    wording = wall.wording;
  }
  if (who || wording) acctMeta = { account: who, budget: wording };
}

/* ── …and where the host keeps nothing: the server ──────────────────────
   VS Code gives the frame no store and mounts the grid again from the answer at every
   turn of the conversation: the likes, the shape filter and the deck were gone (seen,
   2026-10-01). There the view goes to the server instead (selection.put_view), under
   the token of the answer this frame was mounted with — the host hands a remounted
   frame that same answer, and nobody else has it — and the next frame reads it back
   before it writes anything. */
const VIEW_BUDGET = 90 * 1024;   // under the server's VIEW_MAX, envelope included
const READ_WAIT_MS = 2500;       // a slow server costs the reader a moment, not the grid
let viewTimer = null;
let viewPending = false;         // the read is in flight: nothing is written meanwhile
let userActed = false;           // …and a reader who acts meanwhile keeps what they did
/* The token of the answer this frame was mounted with, taken once by restoreUi: a
   shape filter brings a new token with its answer, a remounted frame only the first. */
let viewToken = "";

function hostStores() {
  const oai = window.openai;
  return !!(oai && typeof oai.setWidgetState === "function");
}
function serverViewUrl() {
  return (!hostStores() && selCap && selCap.view && viewToken) ? selCap.view : "";
}
/* The request body, the oldest refinements dropped first as for the host's store, then
   the trail itself: the likes and the deck are worth more than the pages behind. */
function viewBody(view) {
  const wrap = (v) => JSON.stringify({ token: viewToken, sig: v.sig, view: v });
  let body = wrap(view);
  if (body.length <= VIEW_BUDGET) return body;
  const slim = Object.assign({}, view, { trail: trimTrail(VIEW_BUDGET / 2) });
  body = wrap(slim);
  if (body.length <= VIEW_BUDGET) return body;
  slim.trail = [];
  slim.step = 0;
  body = wrap(slim);
  return body.length <= VIEW_BUDGET ? body : "";
}
function serverViewPut(view, now) {
  if (!serverViewUrl() || viewPending || !view || !view.sig) return;
  if (viewTimer) { clearTimeout(viewTimer); viewTimer = null; }
  const send = () => {
    viewTimer = null;
    if (viewPending) return;
    let body = "";
    try { body = viewBody(view); } catch (e) { return; }
    if (!body) return;
    try {
      fetch(selCap.view, { method: "POST", body: body, mode: "cors", credentials: "omit",
                           keepalive: body.length < 60000,   // 64 KiB at most in keepalive
                           headers: { "Content-Type": "text/plain;charset=UTF-8" } })
        .catch(() => {});
    } catch (e) {}
  };
  if (now) send();
  else viewTimer = setTimeout(send, 300);
}
viewSink = serverViewPut;

/* The view kept for the answer on screen, or null. */
function serverViewGet() {
  const sig = resultSig();
  if (!serverViewUrl() || !sig) return Promise.resolve(null);
  let req;
  try {
    req = fetch(selCap.view, { method: "POST", mode: "cors", credentials: "omit",
                               body: JSON.stringify({ token: viewToken, sig: sig }),
                               headers: { "Content-Type": "text/plain;charset=UTF-8" } })
      .then((r) => (r.ok ? r.json() : null))
      .then((j) => ((j && j.view && typeof j.view === "object") ? j.view : null))
      .catch(() => null);
  } catch (e) { return Promise.resolve(null); }
  return Promise.race([req, new Promise((res) => setTimeout(() => res(null), READ_WAIT_MS))]);
}
for (const type of ["pointerdown", "keydown"]) {
  document.addEventListener(type, () => { if (viewPending) userActed = true; }, true);
}

/* Read once, with the first result of this frame's life: a later result is a new
   question, not a place to be put back to. From the host's store; where the host keeps
   nothing, from the server, with every write held until it answers — the selection
   included, which a fresh frame would otherwise post empty over the reader's. */
function restoreUi() {
  if (uiRestored) return;
  uiRestored = true;
  viewToken = (selCap && selCap.token) || "";
  const boot = viewIn(BOOT_STATE);
  if (boot || !serverViewUrl()) { applyView(boot, false); return; }
  viewPending = true;
  if (viewTimer) { clearTimeout(viewTimer); viewTimer = null; }
  ctxHold++;
  serverViewGet().then((v) => {
    viewPending = false;
    if (v && !userActed) applyView(v, true);
    ctxHold--;
    pushContextToModel();
    saveUi();
  });
}

/* Put a view back. The server's copy is not held to the store's fifteen minutes: the
   server keeps it as long as the selection it goes with (VIEW_TTL), and a reader back
   after twenty minutes finds the photos they liked still liked. */
function applyView(ui, fromServer) {
  if (!ui || !ui.at) return;
  if (!fromServer && Date.now() - ui.at > UI_TTL_MS) return;
  if (ui.sig && ui.sig !== resultSig()) return;     // a different answer entirely
  // First: without the token, a restored frame could not correct the server's copy
  // of the selection.
  if (!selCap && ui.cap && ui.cap.token && ui.cap.url) selCap = ui.cap;
  if (ui.coach && typeof ui.coach === "object") {
    for (const k of COACH_STEPS) coach[k] = !!ui.coach[k];
    // Finished is not off: the closing line waits for its cross, reload or not.
    coach.off = !!ui.coach.off;
    coachLast = typeof ui.coach.last === "string" ? ui.coach.last : "";
  }
  // A view written before the server muted the coach does not bring it back.
  if (coachMuted()) coach.off = true;
  if (ui.note) { noteClosed = true; paintNote(); }
  if (ui.reordered) selReordered = true;
  /* The trail first: everything below is about the page on screen. The anchor stays
     the one the host just sent, with freshly signed links, unless the reader had
     filtered it here: a filtered anchor was fetched by the frame and, like a
     refinement, exists nowhere else. */
  const saved = Array.isArray(ui.trail) ? ui.trail : [];
  const savedAnchor = saved[0];
  const anchorFiltered = !!(savedAnchor && normShapes(savedAnchor.shape).length
                            && savedAnchor.photos && savedAnchor.photos.length);
  if (saved.length > 1 || anchorFiltered) {
    const anchor = adoptPage(trail[0]);
    if (anchorFiltered) {
      anchor.photos = savedAnchor.photos;
      anchor.shape = normShapes(savedAnchor.shape);
      if (savedAnchor.cta) anchor.cta = savedAnchor.cta;
      if (savedAnchor.origin) anchor.origin = savedAnchor.origin;
      // `base` stays the host's own, freshly signed answer: where "Every shape" goes.
      for (const sig of (savedAnchor.psigs || [])) {
        if (anchor.psigs.indexOf(sig) === -1) anchor.psigs.push(sig);
      }
    }
    trail.length = 0;
    trail.push(anchor);
    for (const pg of saved.slice(1)) {
      if (pg && pg.photos && pg.photos.length) trail.push(adoptPage(pg));
    }
    step = Math.min(Math.max(0, ui.step || 0), trail.length - 1);
    showStep();
  }
  if (ui.shown && ui.shown > shown) {
    shown = Math.min(ui.shown, PHOTOS.length, gridShownMax());
    capShown();
  }
  if (ui.kept && ui.kept.length) {
    // Across the whole trail: a photo liked two refinements ago is not on this page.
    const byId = new Map();
    for (const page of trail) for (const f of page.photos) if (!byId.has(f.id)) byId.set(f.id, f);
    // One context push for all of them, not one per photo.
    withHeldContext(() => {
      for (const id of ui.kept) {
        const f = byId.get(id);
        if (f) setKeptPhoto(f, true);
      }
    });
  }
  if (!ui.deck || !ui.pid) return;
  let at = -1;
  for (let i = 0; i < shown && i < PHOTOS.length; i++) if (PHOTOS[i].id === ui.pid) { at = i; break; }
  if (at < 0) return;
  openViewer(at);
}

function setKeptPhoto(f, on) {
  if (!f || !f.id) return;
  selSpoken = true;
  if (on) coachDid("like");
  if (on) { kept.add(f.id); keptPhotos.set(f.id, f); }
  else { kept.delete(f.id); keptPhotos.delete(f.id); }
  const card = cardOf(f.id);
  if (card) {
    card.classList.toggle("on", on);
    const pick = card.querySelector(".pick");
    if (pick) pick.innerHTML = on ? ICON.check : ICON.heartS;
  }
  paintSelBlock();
  paintTrailCounts();
  pushContextToModel();
  saveUi();
}
function setKept(i, on) { setKeptPhoto(DECK[i], on); }
function clearKept(exceptId) {
  // One change of mind, one state.
  withHeldContext(() => {
    for (const f of keptList()) if (f.id !== exceptId) setKeptPhoto(f, false);
  });
}
/* The strip's title answers what readers kept asking: yes, the assistant can use
   these, named as the line above the grid names it (Claude.ai, 2026-10-02: the line
   said Claude, the title under it "the assistant"). */
function selTitle(n) {
  const who = assistantName();
  return who
    ? tn(n, "Liked photo — use it directly in the chat with {assistant}",
         "Liked photos — use them directly in the chat with {assistant}", { assistant: who })
    : tn(n, "Liked photo — use it directly in the chat with the assistant",
         "Liked photos — use them directly in the chat with the assistant");
}
/* The liked photos, as a second grid under the results: each tile carries its rank
   and the cross that drops it, and opens the deck over the liked photos alone. */
function paintSelBlock() {
  // Painted again mid-gesture (a like from the deck): the gesture is over.
  if (selDrag) { selDrag = null; selEl.classList.remove("sorting"); }
  const list = keptList();
  // A host with a panel of its own shows them there (embedOpt), not twice.
  if (hostShowsSelection()) { selEl.hidden = true; selEl.innerHTML = ""; return; }
  // Hidden while the deck is open: the deck took the grid's height.
  selEl.hidden = deckOpen || list.length === 0;
  if (!list.length) { selEl.innerHTML = ""; return; }
  const n = list.length;
  selEl.innerHTML =
    '<p class="seltitle">' + ICON.heartSel + "<span>"
    + esc(selTitle(n)) + "</span></p>"
    + '<div class="selhead"><span>'
    + esc(tn(n, "{n} image selected", "{n} images selected", { n: "{b}" }))
        .replace("{b}", "<b>" + fmtN(n) + "</b>") + "</span>"
    + '<span class="selacts">'
    + '<button class="ico clear" type="button" title="' + esc(tx("Clear the selection")) + '"'
    + ' aria-label="' + esc(tx("Clear the selection")) + '">' + ICON.crossS + "</button>"
    + "</span></div>"
    + (n > 1 ? '<p class="selhint">' + esc(tx("Drag to change the order.")) + "</p>" : "")
    + '<div class="selgrid">'
    + list.map((f, i) =>
        '<div class="seltile" role="button" tabindex="0" data-open="' + i + '"'
        + ' aria-label="' + esc(tx("Look through the selection from #{n}", { n: i + 1 })) + '">'
        + '<img src="' + esc(f.thumb) + '" alt="" draggable="false">'
        + '<span class="rk grip" role="button" tabindex="0" data-grip="' + i + '"'
        + ' title="' + esc(tx("Drag to change the order.")) + '"'
        + ' aria-label="' + esc(tx("Move #{n}: drag it, or use the arrow keys", { n: i + 1 })) + '">'
        + ICON.grip + '#' + (i + 1) + "</span>"
        + '<button class="x" type="button" data-drop="' + esc(f.id)
        + '" aria-label="' + esc(tx("Remove #{n} from the selection", { n: i + 1 })) + '">'
        + ICON.crossS + "</button></div>").join("")
    + "</div>";
  const clear = selEl.querySelector(".clear");
  if (clear) clear.addEventListener("click", () => clearKept(null));
  for (const b of selEl.querySelectorAll("[data-drop]")) {
    b.addEventListener("click", (e) => {
      e.stopPropagation();                       // the tile under it opens the deck
      setKeptPhoto(keptPhotos.get(b.getAttribute("data-drop")), false);
    });
  }
  for (const g of selEl.querySelectorAll("[data-grip]")) {
    const at = Number(g.getAttribute("data-grip"));
    g.addEventListener("pointerdown", (e) => startSelDrag(e, g.closest(".seltile"), g));
    g.addEventListener("pointermove", moveSelDrag);
    g.addEventListener("pointerup", endSelDrag);
    g.addEventListener("pointercancel", endSelDrag);
    // The tile under it opens the deck: a press on the handle is not that.
    g.addEventListener("click", (e) => e.stopPropagation());
    g.addEventListener("keydown", (e) => {
      const k = e.key;
      const to = (k === "ArrowLeft" || k === "ArrowUp") ? at - 1
        : ((k === "ArrowRight" || k === "ArrowDown") ? at + 1 : -2);
      if (to === -2) { if (k === "Enter" || k === " ") e.stopPropagation(); return; }
      e.preventDefault();
      e.stopPropagation();
      if (moveKept(at, to)) focusGrip(to);
    });
  }
  for (const t of selEl.querySelectorAll("[data-open]")) {
    const at = Number(t.getAttribute("data-open"));
    // The deck walks a snapshot: dropping a photo mid-review does not renumber it.
    const open = () => { try { openViewer(at, keptList()); } catch (e) {} };
    t.addEventListener("click", open);
    t.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); }
    });
  }
}

/* ── Where the liked photos go ─────────────────────────────────────────────
   Under the name, on every grid: the photos the reader likes are passed on to the
   assistant, named where the server knows which one (`_meta["pexafy/host"]`,
   hosts.assistant_name), else from the host's own name. Not the coach: it says what
   happens to a like, every time. Its cross closes it for the grid on screen, and it
   stays closed when the frame is mounted again (the view keeps `note`). */
let hostMeta = null;          // { assistant } from `_meta["pexafy/host"]`, or null
let noteClosed = false;
/* The assistant of each host the grid knows, by the host's own name (surface()); the
   server's word comes first. An unknown host gets no name: "the assistant". */
const ASSISTANTS = [
  [/chatgpt|openai/, "ChatGPT"], [/claude|anthropic/, "Claude"], [/cursor/, "Cursor"],
  [/visual-studio-code|vscode|copilot/, "GitHub Copilot"], [/goose/, "Goose"],
];
function assistantName() {
  if (hostMeta && hostMeta.assistant) return String(hostMeta.assistant);
  let n = window.openai ? "chatgpt" : "";
  if (!n) { try { const i = app.getHostVersion && app.getHostVersion(); n = slug(i && i.name); } catch (e) {} }
  if (!n) n = surface();
  for (const [re, name] of ASSISTANTS) if (re.test(n)) return name;
  return "";
}
function paintNote() {
  const on = !noteClosed && !!PHOTOS.length && wallEl.hidden;
  noteEl.hidden = !on;
  if (!on) return;
  const who = assistantName();
  noteText.textContent = who
    ? tx("Photos you like are shared with {assistant}", { assistant: who })
    : tx("Photos you like are shared with the assistant");
  noteX.title = tx("Hide this message");
  noteX.setAttribute("aria-label", tx("Hide this message"));
}
noteX.innerHTML = ICON.crossXS;
noteX.addEventListener("click", () => { noteClosed = true; paintNote(); saveUi(); });

/* The bug report: the site's contact form, on "Bug reports", in the reader's language
   where the site has it (the same codes, widget_i18n.LANGUAGES). */
function bugUrl() {
  return PEXAFY_HOME + (LANG !== "en" && I18N[LANG] ? "/" + LANG : "") + "/contact/Bug%20reports";
}
function paintBug() {
  bugBtn.title = tx("Report a bug");
  bugBtn.setAttribute("aria-label", tx("Report a bug"));
}
bugBtn.innerHTML = ICON.bug;
paintBug();
bugBtn.addEventListener("click", () => openLink(bugUrl(), "bug"));

/* The language changed after something was drawn: draw it again in the new one. */
function relocalize() {
  localizeStatic();
  paintBug();
  paintNote();
  if (!PHOTOS.length) return;
  paintGrid();
  paintTrail();
  paintShape();
}

/* ── Reordering the liked photos ──────────────────────────────────────────
   The rank is the order the assistant is given, #1 first: the reader sets it by
   dragging a photo by its handle, or with the arrow keys on it. Pointer events, as on
   the try-it page's list: mouse, finger and pen alike. While a photo is dragged the
   others take the slots they will have once it is dropped. */
let selDrag = null;
/* Whether the reader has rearranged the liked photos: the context then says so, and the
   assistant stops calling #1 "the one liked first" (seen in ChatGPT, 2026-10-02). */
let selReordered = false;

function moveKept(from, to) {
  const list = keptList();
  if (to < 0 || to >= list.length || to === from) return false;
  list.splice(to, 0, list.splice(from, 1)[0]);
  keptPhotos.clear();
  for (const f of list) keptPhotos.set(f.id, f);
  selSpoken = true;
  selReordered = true;
  paintSelBlock();
  pushContextToModel();
  saveUi();
  return true;
}
function focusGrip(n) {
  const g = selEl.querySelector('[data-grip="' + n + '"]');
  if (g) { try { g.focus(); } catch (e) {} }
}
function startSelDrag(e, tile, handle) {
  if (e.button || !tile) return;
  const tiles = Array.from(selEl.querySelectorAll(".seltile"));
  const from = tiles.indexOf(tile);
  if (from < 0 || tiles.length < 2) return;
  e.preventDefault();
  selDrag = { tile: tile, tiles: tiles, rects: tiles.map((t) => t.getBoundingClientRect()),
              from: from, to: from, x0: e.clientX, y0: e.clientY, moved: false };
  try { handle.setPointerCapture(e.pointerId); } catch (err) {}
  tile.classList.add("dragging");
  selEl.classList.add("sorting");
}
function moveSelDrag(e) {
  const d = selDrag;
  if (!d) return;
  const dx = e.clientX - d.x0, dy = e.clientY - d.y0;
  if (!d.moved && Math.abs(dx) + Math.abs(dy) < 4) return;
  d.moved = true;
  d.tile.style.transform = "translate(" + dx + "px," + dy + "px) scale(1.05)";
  // The slot under the photo: the nearest centre.
  const r0 = d.rects[d.from];
  const cx = r0.left + r0.width / 2 + dx, cy = r0.top + r0.height / 2 + dy;
  let to = d.from, best = Infinity;
  d.rects.forEach((r, i) => {
    const ax = r.left + r.width / 2 - cx, ay = r.top + r.height / 2 - cy;
    if (ax * ax + ay * ay < best) { best = ax * ax + ay * ay; to = i; }
  });
  if (to === d.to) return;
  d.to = to;
  d.tiles.forEach((t, i) => {
    if (t === d.tile) return;
    let slot = i;
    if (d.from < to && i > d.from && i <= to) slot = i - 1;
    else if (d.from > to && i >= to && i < d.from) slot = i + 1;
    const a = d.rects[i], b = d.rects[slot];
    t.style.transform = slot === i ? "" : "translate(" + (b.left - a.left) + "px," + (b.top - a.top) + "px)";
  });
}
function endSelDrag() {
  const d = selDrag;
  if (!d) return;
  selDrag = null;
  selEl.classList.remove("sorting");
  d.tiles.forEach((t) => { t.style.transform = ""; t.classList.remove("dragging"); });
  if (d.moved && d.to !== d.from) moveKept(d.from, d.to);
}

/* ── The trail: refining without leaving the grid ─────────────────────────
   Where the host proxies tool calls (`serverTools`, or ChatGPT's
   `window.openai.callTool`), ≈ runs the by-image search from the widget and paints
   the answer in this grid; the model is told through the context, not a turn in the
   thread. Each page stays on the trail across the head, and every chip is a way
   back. At most six refinements deep. */
const MAX_REFINE = 6;
const trail = [];        // pages (see newPage); trail[0] is the anchor, ref = null
let step = 0;            // which one is on screen
let refining = false;    // a call is out, or a card is on its way off the deck

function canProxyTools() {
  return hostCan("serverTools")
    || !!(window.openai && typeof window.openai.callTool === "function");
}
/* Throws when the host cannot run the tool, so ≈ never silently does nothing: the
   caller turns the error into a toast. */
async function callServerTool(name, args) {
  if (hostCan("serverTools")) return await app.callServerTool({ name: name, arguments: args });
  const oai = window.openai;
  if (oai && typeof oai.callTool === "function") return await oai.callTool(name, args);
  throw new Error(tx("This host cannot run a search from the grid"));
}

/* The ceiling is the depth of the page on screen, not the trail's length: from a
   page further back, going on replaces the steps ahead. */
function refineExhausted() { return step >= MAX_REFINE; }

/* The one photo wearing ≈ on the page on screen, read off the trail: the photo that
   led forward from this page, or else the one this page was asked from (it leads the
   grid). At most one, and going back takes it away with the steps ahead. */
function starredHere() {
  const fwd = trail[step + 1];
  if (fwd && fwd.ref) return fwd.ref.id;
  const here = trail[step];
  return (here && here.ref) ? here.ref.id : null;
}

/* The photos of one result set, in grid order. `lead`, the photo a refinement was
   asked from, goes first so the new grid opens on it; it is a copy, since the same
   object still sits in the page it came from. */
function buildPhotos(sc, lead) {
  const raw = (sc && sc.data) || [];
  // From `_meta` (learnLinks); `sc.cta` is where earlier servers put it, in the answers
  // an older conversation still holds.
  const cta = ctaMeta || (sc && sc.cta) || null;
  // Before the photos: fields() builds each page URL from the adopted site.
  if (cta && cta.url) adoptSite(cta.url);
  let out = [];
  if (Array.isArray(raw)) {
    for (const photo of raw.slice(0, GRID_MAX)) {
      const f = fields(photo || {});
      if (f.thumb) out.push(f);
    }
  }
  if (lead) {
    const head = Object.assign({}, lead);
    out = [head].concat(out.filter(f => f.id !== head.id)).slice(0, GRID_MAX);
  }
  return { photos: out, cta: cta };
}

/* How many of one page's photos are liked: what each stop on the trail was worth. */
function likedIn(n) {
  const s = trail[n];
  if (!s) return 0;
  let c = 0;
  for (const f of s.photos) if (kept.has(f.id)) c++;
  return c;
}
function countBadge(n) {
  const c = likedIn(n);
  return c ? '<b class="tn">' + c + "</b>" : "";
}
/* The counts alone, without rebuilding the trail: a rebuild would restart the
   anchor's beat, which plays when the trail grows and at no other time. */
function paintTrailCounts() {
  for (const b of trailEl.querySelectorAll("[data-step]")) {
    const n = Number(b.getAttribute("data-step"));
    const badge = b.querySelector(".tn");
    const c = likedIn(n);
    if (c && badge) badge.textContent = c;
    else if (c) b.insertAdjacentHTML("beforeend", '<b class="tn">' + c + "</b>");
    else if (badge) badge.remove();
  }
}

/* One chip per page, the one on screen ringed. Nothing below two pages: a trail of
   one is no way back. */
function paintTrail() {
  const on = trail.length > 1;
  trailEl.hidden = !on;
  if (!on) { trailEl.innerHTML = ""; return; }
  let html = '<button class="tstep anchor" type="button" data-step="0"'
    + ' title="' + esc(tx("Back to the first search")) + '" aria-label="'
    + esc(tx("Back to the first search")) + '">'
    + ICON.anchor + countBadge(0) + "</button>";
  for (let i = 1; i < trail.length; i++) {
    const ref = trail[i].ref || {};
    const label = ref.author ? tx("Back to the photos like {author}'s", { author: ref.author })
                             : tx("Back to the photos like this one");
    html += '<i class="tsep"></i>'
      + '<button class="tstep" type="button" data-step="' + i + '" title="' + esc(label)
      + '" aria-label="' + esc(label) + '">'
      + '<span class="tw"><img src="' + esc(ref.thumb || "") + '" alt=""></span>'
      + countBadge(i) + "</button>";
  }
  trailEl.innerHTML = html;
  const cur = trailEl.querySelector('[data-step="' + step + '"]');
  if (cur) cur.classList.add("on");
  for (const b of trailEl.querySelectorAll("[data-step]")) {
    b.addEventListener("click", () => goToStep(Number(b.getAttribute("data-step"))));
  }
}

/* The anchor beats three times when the trail grows, when going back starts to
   matter, then holds still. */
function beatTheAnchor() {
  const a = trailEl.querySelector(".tstep.anchor");
  if (!a || REDUCED) return;
  a.classList.remove("hint");
  void a.offsetWidth;                       // restart it on the next refinement too
  a.classList.add("hint");
  setTimeout(() => a.classList.remove("hint"), 3600);
}

/* Put a page on screen. Everything the grid reads comes from the page, so going back
   is a repaint, not a round trip. */
function showStep() {
  const s = trail[step] || { photos: [], cta: null };
  PHOTOS = s.photos;
  footUrl = (s.cta && s.cta.url) || PEXAFY_HOME;
  footLabel = (s.cta && s.cta.label) || "Open in Pexafy";
  // The shape filter re-asks the question of the page on screen.
  if (s.origin) ORIGIN = s.origin;
  shown = gridFirst();
  paintGrid();
  paintTrail();
  paintShape();
  pushContextToModel();
}

/* Going back is not undoing: the pages ahead stay on the trail until a refinement is
   made from here. */

/* ── Turning a page ───────────────────────────────────────────────────────
   The grid leaves in the direction of travel and the next page comes in from the
   other side, slightly tilted: the movement shows that there are pages. */
function stepExists(n) { return n >= 0 && n < trail.length; }

function snapGrid() {
  gridEl.style.transition = "transform .22s cubic-bezier(.2,.8,.2,1)";
  gridEl.style.transform = "";
}
function restGrid() {
  gridEl.style.transition = "";
  gridEl.style.transform = "";
  gridEl.style.opacity = "";
}

/* The incoming half of a page turn, which is also how a refinement arrives. `dir` +1:
   the reader went forward, so the new page comes from the right. */
function enterPage(dir) {
  if (REDUCED) { restGrid(); return; }
  gridEl.style.transition = "none";
  gridEl.style.transform = "translateX(" + (dir * 40) + "%) rotate(" + (dir * 1.5) + "deg)";
  gridEl.style.opacity = "0";
  // Two frames: one to take the start state, one to animate from it.
  requestAnimationFrame(() => requestAnimationFrame(() => {
    gridEl.style.transition = "transform .28s cubic-bezier(.2,.8,.2,1), opacity .24s ease";
    gridEl.style.transform = "";
    gridEl.style.opacity = "";
    setTimeout(restGrid, 320);
  }));
}

function goToStep(n) {
  if (refining || !stepExists(n) || n === step) { snapGrid(); return; }
  const dir = n > step ? 1 : -1;          // +1: the page leaves to the left
  if (REDUCED) { restGrid(); step = n; showStep(); return; }
  gridEl.style.transition = "transform .17s ease-in, opacity .17s ease-in";
  gridEl.style.transform = "translateX(" + (-dir * 40) + "%) rotate(" + (-dir * 1.5) + "deg)";
  gridEl.style.opacity = "0";
  setTimeout(() => {
    step = n;
    showStep();                            // the new page, painted off screen
    enterPage(dir);
  }, 170);
}

/* ≈ from inside the widget: a by-image search from this photo, painted in the grid
   on screen as a new page of the trail. */
async function refineFrom(f) {
  if (!f || !f.id || refining) return false;
  const here = trail[step];
  if (here && here.ref && here.ref.id === f.id) {
    toast(tx("Already showing photos like this one"));
    return false;
  }
  if (refineExhausted()) { toast(tx("Six refinements is the limit")); return false; }
  // A call known to be refused is not made: the error of a call the widget makes is
  // printed raw in the conversation, and the account panel says it in words. The
  // guard expires (WALL_GUARD_MS), because an allowance comes back and this frame
  // only learns it from a tool result.
  if (acctBudget && acctBudget.state === "exhausted"
      && Date.now() - wallSeenAt < WALL_GUARD_MS) {
    toast(acctBudget.title || tx("The allowance is used up"));
    fillPop();
    openPop();
    return false;
  }
  refining = true;
  gridEl.classList.add("busy");
  headEl.classList.add("busy");
  try {
    /* The photo, with the words of the page's search (wordsOf): without them a
       refinement drifts off its subject within a couple of steps. A search that had
       no words sends the photo alone. */
    const args = { photo_id: f.id };
    const words = wordsOf(trail[step]);
    if (words) args.__ARG_SENTENCE__ = words;
    const res = await callServerTool("__TOOL_BY_IMAGE__", args);
    try { learnLinks(res && (res._meta || res.meta)); } catch (e) {}
    const sc = res && res.structuredContent;
    // A refinement spends a search: the account corner follows.
    if (sc && (sc.account || sc.budget || acctMeta)) paintAccount(sc);
    const built = buildPhotos(sc, f);
    // The wall, reached from inside: the grid on screen stays, the panel says why.
    const spent = budgetOf(sc);
    if (!built.photos.length && spent && spent.state === "exhausted") {
      toast(spent.title || tx("The allowance is used up"));
      fillPop();
      openPop();
      return false;
    }
    // One photo back is the reference itself: the search found nothing.
    if (built.photos.length < 2) { toast(tx("No similar photos came back")); return false; }
    trail.length = step + 1;                 // everything ahead is the branch not taken
    // The page keeps the payload's signature: render() must recognise this answer if
    // the host hands it back as a tool result.
    trail.push(newPage(f, built, sc));
    step = trail.length - 1;
    showStep();
    // It arrives like a page turn, which is how readers find out there are pages.
    enterPage(1);
    beatTheAnchor();
    return true;
  } catch (e) {
    toast((e && e.message) ? e.message : tx("Could not fetch similar photos"));
    return false;
  } finally {
    refining = false;
    gridEl.classList.remove("busy");
    headEl.classList.remove("busy");
  }
}

/* ── Shape: the same question, asked for some formats ─────────────────────
   Pressing "Portrait" re-runs the question of the page on screen (its origin, see
   origin.py) with the shape attached, and a full grid of portraits comes back;
   hiding tiles would leave the few portraits the page had. The unfiltered page stays
   on the same step (`base`), so "Every shape" costs no call. A filter is not a trail
   step: it would use up the refinements. */

/* One trail page, however it was produced. `base` is the page as its question
   produced it (already filtered when the assistant asked for a shape): what "Every
   shape" returns to. */
function newPage(ref, built, sc) {
  const sig = payloadSig(sc);
  return {
    ref: ref,
    photos: built.photos,
    base: built.photos,
    cta: built.cta,
    baseCta: built.cta,
    // The allowance panel's wording that came with this answer (learnLinks), which the
    // anchor's stub keeps for a reloaded frame (packAnchor).
    wording: (acctMeta && acctMeta.budget) || null,
    shape: normShapes(ORIGIN && ORIGIN.shape),
    origin: ORIGIN,
    // Every payload signature this page has shown (a filtered page is another
    // payload), so render() recognises any of them handed back as a tool result.
    psigs: sig ? [sig] : [],
  };
}

let shaping = false;   // a shape call is out

/* Hidden rather than inert where it cannot work: a host that does not proxy tool
   calls, or a page with no replayable question (a search from an uploaded file). */
function canShape() {
  const here = trail[step];
  return !!(here && here.origin && here.origin.tool && canProxyTools());
}

function paintShape() {
  if (!canShape()) { shapeWrap.hidden = true; closeShapePop(); return; }
  const here = trail[step];
  const on = normShapes(here.shape);
  shapeWrap.hidden = false;
  shapeBtn.classList.toggle("on", on.length > 0);
  // The button wears the shapes it filters on, and the generic mark otherwise.
  shapeGl.innerHTML = shapesGlyph(on, 16, 2);
  const label = on.length ? tx("{shapes} only", { shapes: shapesLabel(on) }) : tx("Filter by shape");
  shapeBtn.setAttribute("aria-label", label);
  shapeBtn.title = label;
  if (!shapePop.hidden) fillShapePop();
}

/* Boxes to tick: several shapes can be on, and a tick leaves the menu open.
   `pending` holds the ticks not served yet (the call waits a beat, see toggleShape),
   so the menu shows them ahead of the page. "Every shape" is one press and closes it. */
let pending = null;
function fillShapePop() {
  const on = normShapes(pending !== null ? pending : (trail[step] && trail[step].shape));
  const every = on.length === 0;
  let html = '<button class="shapeopt' + (every ? " on" : "") + '" type="button"'
    + ' role="menuitemradio" aria-checked="' + every + '" data-shape="">'
    + '<span class="sgl">' + ICON.everyShape + "</span><span>" + esc(tx("Every shape")) + "</span>"
    + (every ? '<span class="stick">' + ICON.chooseS + "</span>" : "") + "</button>"
    + '<div class="shapesep"></div>';
  for (const shape of SHAPES) {
    const sel = on.indexOf(shape) !== -1;
    html += '<button class="shapeopt' + (sel ? " on" : "") + '" type="button"'
      + ' role="menuitemcheckbox" aria-checked="' + sel + '" data-shape="' + shape + '">'
      + '<span class="sgl">' + shapeGlyph(shape, 16, 2) + "</span><span>" + esc(shapeName(shape))
      + "</span>" + '<span class="sbox">' + (sel ? ICON.tickS : "") + "</span></button>";
  }
  shapePop.innerHTML = html;
  for (const b of shapePop.querySelectorAll("[data-shape]")) {
    b.addEventListener("click", (e) => {
      e.stopPropagation();
      const shape = b.getAttribute("data-shape") || "";
      if (!shape) { applyShapes([]); return; }
      toggleShape(shape);
    });
  }
}

function openShapePop() {
  fillShapePop();
  shapePop.hidden = false;
  shapeBtn.setAttribute("aria-expanded", "true");
}
function closeShapePop() {
  shapePop.hidden = true;
  shapeBtn.setAttribute("aria-expanded", "false");
}

/* A tick, and the call waits a beat: Landscape then Square is one question, not two
   calls. 420ms is longer than the gap between two ticks. */
const SHAPE_SETTLE_MS = 420;
let shapeTimer = null;
function toggleShape(shape) {
  const here = trail[step];
  if (!here || !here.origin) return;
  const now = shapeList(pending !== null ? pending : here.shape);
  const at = now.indexOf(shape);
  if (at === -1) now.push(shape); else now.splice(at, 1);
  pending = shapeList(now);
  fillShapePop();                          // the tick shows at once; the page follows
  clearTimeout(shapeTimer);
  shapeTimer = setTimeout(() => { shapeTimer = null; applyShapes(pending); }, SHAPE_SETTLE_MS);
}

async function applyShapes(list) {
  const here = trail[step];
  const want = normShapes(list);
  // A call already out: what was ticked meanwhile is served when it lands, at the end.
  if (!here || !here.origin || shaping || refining) return;
  if (sameShapes(here.shape, want)) {
    pending = null;
    if (!want.length) closeShapePop(); else fillShapePop();
    return;
  }

  // Every shape: the page is already in hand. No call, no quota, no wait.
  if (!want.length) {
    pending = null;
    closeShapePop();
    here.photos = here.base;
    here.cta = here.baseCta;
    here.shape = [];
    ORIGIN = here.origin;
    showStep();
    // Written at once: a shape change costs seconds and a tool call, and the host
    // may reload the frame meanwhile.
    saveUi(true);
    return;
  }

  // A call known to be refused is not made (see refineFrom).
  if (acctBudget && acctBudget.state === "exhausted"
      && Date.now() - wallSeenAt < WALL_GUARD_MS) {
    pending = null;
    fillShapePop();
    toast(acctBudget.title || tx("The allowance is used up"));
    fillPop();
    openPop();
    return;
  }

  shaping = true;
  gridEl.classList.add("busy");
  headEl.classList.add("busy");
  try {
    const args = Object.assign({}, here.origin.args);
    args[ORIENT_ARG] = want;
    const res = await callServerTool(here.origin.tool, args);
    try { learnLinks(res && (res._meta || res.meta)); } catch (e) {}
    const sc = res && res.structuredContent;
    // A filter spends a search too.
    if (sc && (sc.account || sc.budget || acctMeta)) paintAccount(sc);
    const built = buildPhotos(sc, null);
    if (!built.photos.length) {
      // Two empties, two sentences: the allowance is gone, or no photo of these
      // shapes matched. The grid on screen stays either way.
      const spent = budgetOf(sc);
      if (spent && spent.state === "exhausted") {
        toast(spent.title || tx("The allowance is used up"));
        fillPop();
        openPop();
      } else {
        toast(LANG === "en"
              ? "No " + shapesLabel(want).toLowerCase().replace(/ \\+ /g, " or ") + " photos for this search"
              : tx("No {shapes} photos for this search",
                   { shapes: listOr(shapeList(want).map(shapeName)) }));
      }
      ORIGIN = here.origin;   // the call overwrote it; this page did not change
      pending = null;         // and the ticks that asked for it are dropped with it
      paintShape();
      return;
    }
    here.photos = built.photos;
    here.cta = built.cta || here.baseCta;
    here.shape = want;
    const sig = payloadSig(sc);
    if (sig && here.psigs.indexOf(sig) === -1) here.psigs.push(sig);
    // The page keeps its unfiltered question, so the next press can ask for another
    // shape rather than narrow this one.
    ORIGIN = here.origin;
    showStep();
    saveUi(true);

    // The grid changed without a page turn: it arrives the same way.
    enterPage(1);
  } catch (e) {
    toast((e && e.message) ? e.message : tx("Could not filter by shape"));
    ORIGIN = here.origin;
    pending = null;
    paintShape();
  } finally {
    shaping = false;
    gridEl.classList.remove("busy");
    headEl.classList.remove("busy");
    // A tick made while the call was out: the menu shows it, the page does not yet.
    if (pending !== null && !sameShapes(pending, here.shape)) applyShapes(pending);
    else pending = null;
  }
}

shapeBtn.addEventListener("click", (e) => {
  e.stopPropagation();
  if (!shapePop.hidden) { closeShapePop(); return; }
  openShapePop();
});
document.addEventListener("click", (e) => {
  if (!shapePop.hidden && !shapePop.contains(e.target)) closeShapePop();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !shapePop.hidden) { closeShapePop(); shapeBtn.focus(); }
});

/* ── The deck ─────────────────────────────────────────────────────────────
   One photo at a time, over a blur of itself. A swipe right likes, left skips, up
   asks for more like it (≈), down closes; the buttons do the same, and so do the
   keys: → like, ← skip, ↑ ≈, ↓ or Backspace back one photo, Esc close.
   DECK is what it walks: the tiles on screen, or the liked photos when the
   selection block opened it. Only the grid reads PHOTOS. */
let DECK = [];
let reviewing = false;   // the deck is walking the liked photos, not the grid
let idx = 0, deckOpen = false, coached = false, plateRO = null;
let keptOnOpen = 0;      // how many photos were liked when this deck opened
let selTaught = false;   // …and whether the reader has been shown where they go
// How far the last gesture went at its furthest, not where it ended: a drag that
// goes out and comes back must not count as a tap on the ground.
let dragMoved = 0;
const undoStack = [];
const REDUCED = (window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches) || false;

/* The next photos load while this one is on screen: a swipe takes 300ms, a 1280w
   file longer. */
const prefetched = new Set();
function prefetch(list, i) {
  const f = list[i];
  if (!f || !f.large || prefetched.has(f.large)) return;
  prefetched.add(f.large);
  const im = new Image();
  im.decoding = "async";
  im.src = f.large;
}

function backdrop(url) {
  const layers = viewerEl.querySelectorAll(".vback i");
  const next = layers[0].classList.contains("on") ? layers[1] : layers[0];
  const prev = next === layers[0] ? layers[1] : layers[0];
  next.style.backgroundImage = url ? 'url("' + url + '")' : "none";
  next.classList.add("on"); prev.classList.remove("on");
}

/* ── Opening and closing a photo ──────────────────────────────────────────
   The deck takes the grid's place and height, so the widget keeps its size and the
   thread does not move; on a desktop it asks the host for nothing (going from full
   screen back to inline made ChatGPT scroll to the top of the conversation). A phone
   is the exception: see goFull.
   Its height is the largest of a floor (DECK_MIN, clear of the compact rule at 470px),
   the grid it covers, and the tallest deck already shown, so a photo is the same size
   wherever it is opened, a short refined grid included; capped at DECK_MAX, about a
   screen, past which the photo floats in empty ground. */
const DECK_MIN = 520;
const DECK_MAX = 800;
let deckH = 0;

/* ── Full screen, on a phone ──────────────────────────────────────────────
   Inline on a phone the deck gets a postcard's height, and its swipes fight the
   conversation's scroll. So on a phone only (the host's `platform`) it asks for full
   screen, and gives it back on close. A request: a host that answers `inline` leaves
   the inline deck as it was. The reader can also leave by the host's own control,
   which the widget learns only from `displayMode` in the host context. */
let wentFull = false;      // the host granted full screen for the deck that is open
let leavingFull = false;   // we asked for `inline`; ignore the context echo that follows
let fullSeen = false;      // the host has since CONFIRMED full screen in its own context
let fullAt = 0;            // when it granted it
/* An unconfirmed `inline` right after the grant is the state from before the request
   (in flight, or mid-transition): taken at its word it would undo the full screen at
   once. Ignored for this long. */
const FULL_ECHO_MS = 1800;

function hostCtx() {
  try { return (app.getHostContext && app.getHostContext()) || null; } catch (e) { return null; }
}

/* ── The name, drawn as a logo where the host draws none ──────────────────
   ChatGPT and Claude show the app's name and logo above the frame, and OpenAI's
   guidelines forbid a second one inside it ("Do not include your logo as part of the
   response"): plain text there. Any other host (VS Code, Cursor…) shows nothing above
   the grid, so the name takes the site's logo colours — a rule served to those hosts
   only (LOGO_CSS, hosts.LogoWhereTheHostDrawsNone): the page ChatGPT gets has no
   gradient at all, and the class alone draws nothing. After connect(): the host names
   itself in the handshake (hostInfo), read here afresh rather than through surface(),
   which keeps the first answer it gave. */
function hostShowsAppIdentity() {
  if (window.openai) return true;
  let n = "";
  try { const i = app.getHostVersion && app.getHostVersion(); n = slug(i && i.name); } catch (e) {}
  if (!n) n = surface();
  return /claude|anthropic/.test(n);
}
function paintBrand() {
  brandEl.classList.toggle("logo", !hostShowsAppIdentity());
}

/* ── A host that is Pexafy's own page ─────────────────────────────────────
   The try-it page on pexafy.com plays the host and does two of the grid's jobs
   itself: it shows the liked photos in a ranked panel of its own, and it runs the
   coach once per visitor. It says so under `pexafy` in its host context (the SDK
   keeps unknown keys); a host that says nothing changes nothing. */
function embedOpt(name) {
  const o = (hostCtx() || {}).pexafy;
  return (o && typeof o === "object") ? o[name] : undefined;
}
function hostShowsSelection() { return embedOpt("selection") === "host"; }
function hostMutesCoach() { return embedOpt("coach") === false; }
/* The server's word on the coach (coach.py): `show: false` for a reader who has met it
   in any grid before — it plays once per person. The frame remembers nothing past one
   answer (see saveUi); the server does. */
function serverMutesCoach() {
  return !!coachMeta && coachMeta.show === false;
}

/* The first time this frame shows the coach, the server is told, and no later answer
   will show it again (coach.py). Once per frame; `text/plain` like publishSelection,
   and a failed post is not retried: the next grid coaching again is the lesser harm. */
let coachReported = false;
function reportCoachShown() {
  if (coachReported || !coachMeta || !coachMeta.url || !coachMeta.token) return;
  coachReported = true;
  try {
    fetch(coachMeta.url, { method: "POST", body: JSON.stringify({ token: coachMeta.token }),
                           mode: "cors", credentials: "omit", keepalive: true,
                           headers: { "Content-Type": "text/plain;charset=UTF-8" } })
      .catch(() => {});
  } catch (e) {}
}
function coachMuted() { return hostMutesCoach() || serverMutesCoach(); }

/* A phone, in the host's own words. When the host does not say, a coarse pointer on
   a narrow viewport stands in for it; no user agent is parsed. */
function isPhone() {
  const c = hostCtx();
  const p = c && c.platform;
  if (p) return p === "mobile";
  try {
    return window.matchMedia("(pointer: coarse)").matches && (window.innerWidth || 0) <= 540;
  } catch (e) { return false; }
}

/* A host that lists its modes says which exist; one that lists none answers the
   request itself, and a refusal costs one caught promise. */
function canGoFull() {
  const m = (hostCtx() || {}).availableDisplayModes;
  if (Array.isArray(m) && m.length) return m.indexOf("fullscreen") !== -1;
  return true;
}

/* Full screen: the viewport's height, less the insets the host reports, instead of
   the grid's height; sizeCards() refits the photo. */
function paintFullscreen(on) {
  viewerEl.classList.toggle("vfull", !!on);
  if (on) {
    const ins = (hostCtx() || {}).safeAreaInsets || {};
    const px = v => (typeof v === "number" && v > 0 ? v : 0) + "px";
    viewerEl.style.setProperty("--safe-t", px(ins.top));
    viewerEl.style.setProperty("--safe-b", px(ins.bottom));
    viewerEl.style.setProperty("--safe-l", px(ins.left));
    viewerEl.style.setProperty("--safe-r", px(ins.right));
    viewerEl.style.height = "";
  } else {
    viewerEl.style.height = deckOpen ? deckH + "px" : "";
  }
  sizeCards();
}

async function goFull() {
  if (wentFull || !deckOpen || !isPhone() || !canGoFull()) return false;
  // Already full screen (a frame reloaded inside the full-screen container): adopt
  // it rather than ask again.
  if ((hostCtx() || {}).displayMode === "fullscreen") {
    wentFull = true; fullSeen = true; fullAt = Date.now();
    paintFullscreen(true);
    return true;
  }
  let granted;
  try {
    const r = await app.requestDisplayMode({ mode: "fullscreen" });
    granted = r && (r.mode || (r.result && r.result.mode));
  } catch (e) { return false; }                 // the host refused; the inline deck stands
  if (granted && granted !== "fullscreen") return false;
  if (!deckOpen) {                              // closed while the host was answering
    wentFull = true; leaveFull(); return true;
  }
  wentFull = true;
  fullSeen = false;
  fullAt = Date.now();
  paintFullscreen(true);
  return true;
}

function leaveFull() {
  if (!wentFull) return;
  wentFull = false;
  fullSeen = false;
  leavingFull = true;
  paintFullscreen(false);
  try {
    const p = app.requestDisplayMode({ mode: "inline" });
    if (p && p.catch) p.catch(() => {});
  } catch (e) {}
  setTimeout(() => { leavingFull = false; }, 500);
}

/* The host's own control can leave full screen, and the widget hears it only here.
   A notification never closes the photo, it only switches between screen and box
   (an echo of the old state would otherwise close it as it opens); an unconfirmed
   `inline` within FULL_ECHO_MS of the grant is discarded. The reader leaves the
   photo by the back arrow, a swipe down, Esc or the last card. */
function syncDisplayMode(ctx) {
  const mode = ctx && ctx.displayMode;
  if (!wentFull || leavingFull || !mode) return;
  if (mode === "fullscreen") {                 // confirmed; insets can change on rotate
    fullSeen = true;
    paintFullscreen(true);
    return;
  }
  if (!fullSeen && Date.now() - fullAt < FULL_ECHO_MS) return;
  wentFull = false;
  fullSeen = false;
  paintFullscreen(false);
}

/* The height a host lets the frame have, when it caps it: the context says so
   (`containerDimensions`), or the page runs past a frame that did not grow with it —
   VS Code shows about a third of a screen and scrolls inside (measured 2026-10-01),
   where a deck the grid's height had its verbs under the frame's edge. 0: no cap. */
function frameCap() {
  const c = (hostCtx() || {}).containerDimensions || {};
  const said = [c.maxHeight, c.height].filter(v => typeof v === "number" && v > 0);
  if (said.length) return Math.min.apply(null, said);
  const doc = document.documentElement;
  const inner = window.innerHeight || 0;
  return (inner > 0 && doc.scrollHeight > inner + 4) ? inner : 0;
}

function openViewer(i, list) {
  let h = Math.min(DECK_MAX, Math.max(DECK_MIN, deckH,
                   gridEl.offsetHeight + headEl.offsetHeight
                   + moreEl.offsetHeight + selEl.offsetHeight));
  // A capped frame: the deck fits it, verbs included (revealed below, once painted).
  const cap = frameCap();
  if (cap && h > cap) h = cap;
  deckH = h;
  coached = false;                       // the sway plays once per opening
  // A list: the selection block opened it. Otherwise the deck walks the tiles on
  // screen, and "See N more" grows both.
  reviewing = !!(list && list.length);
  DECK = reviewing ? list : PHOTOS.slice(0, shown);
  idx = i; deckOpen = true; undoStack.length = 0;
  saveUi(true);                              // before goFull(), which can cost us the frame
  keptOnOpen = kept.size;
  viewerEl.style.height = h + "px";
  headEl.hidden = true; gridEl.hidden = true;
  moreEl.hidden = true; selEl.hidden = true;
  viewerEl.hidden = false;
  coachDid("tap");                           // before the deck paints its own line
  paintDeck();
  /* Into view: the deck replaces the whole grid from its top and is shorter, so a
     photo opened low in a tall grid would open above the screen. Except on a phone
     about to go full screen, where scrolling the host's thread as it opens full
     screen dismisses it: there the scroll waits for the host's answer. */
  if (isPhone() && canGoFull()) {
    goFull().then(ok => { if (!ok && deckOpen) reveal(viewerEl, "center"); });
  } else {
    reveal(viewerEl, "center");
  }
}

/* ── Fitting a photograph to the deck ─────────────────────────────────────
   Each card takes its photo's proportions at the largest size the deck allows. CSS
   cannot fit a ratio into a box bounded on both axes, so it is computed. */
const DEFAULT_AR = 3 / 2;

/* The largest box of ratio `ar` that fits in dw × dh. */
function fitBox(ar, dw, dh) {
  let w = dw, h = dw / ar;
  if (h > dh) { h = dh; w = dh * ar; }
  return [Math.round(w), Math.round(h)];
}

/* Size every card to its photo and cut the deck to the top one, so the buttons ride
   right under the photo and the pair stays centred. */
function sizeCards() {
  const deck = viewerEl.querySelector(".vdeck");
  const body = viewerEl.querySelector(".vbody");
  const ctl = viewerEl.querySelector(".vctl");
  if (!deck || !body) return;
  const availW = body.clientWidth;
  // Measured off the body, never off the deck, whose height is this function's output.
  const availH = body.clientHeight - (ctl && !ctl.hidden ? ctl.offsetHeight + 10 : 0);
  if (!availW || availH < 80) return;         // not laid out yet — load/resize retries
  const top = deck.querySelector(".vcard.top .shot");
  const ar0 = (top && parseFloat(top.dataset.ar)) || DEFAULT_AR;
  const deckH = fitBox(ar0, availW, availH)[1];
  deck.style.height = deckH + "px";
  for (const shot of deck.querySelectorAll(".shot")) {
    const ar = parseFloat(shot.dataset.ar) || DEFAULT_AR;
    const box = fitBox(ar, availW, deckH);
    shot.style.width = box[0] + "px";
    shot.style.height = box[1] + "px";
    shot.classList.toggle("narrow", box[0] < 420);
  }
}
function watchDeck() {
  if (plateRO || !("ResizeObserver" in window)) return;
  // A host resize or a phone rotation refits the cards and the coach's bubble.
  plateRO = new ResizeObserver(() => { sizeCards(); placeGuide(); });
  plateRO.observe(viewerEl.querySelector(".vbody"));
}
function closeViewer() {
  deckOpen = false;
  reviewing = false;
  /* In this order. Giving the screen back may reload the frame (a host re-parents
     the iframe both ways), so the state is written first, or the reloaded frame
     would open the deck again; the grid is then repainted, and measured, inline. */
  saveUi(true);
  leaveFull();
  viewerEl.hidden = true;
  headEl.hidden = false; gridEl.hidden = false;
  // Each decides whether it shows: the likes may have emptied, the grid may be full.
  paintSlot();
  paintSelBlock();
  paintCoach();                              // the line under the grid, for the step that is next
  viewerEl.style.height = "";
  saveUi();                                  // the repaints above may have changed it
  /* A deck that changed the likes says where they went, and the first time it goes
     there too: the strip is below the fold. */
  const n = kept.size;
  if (n && n !== keptOnOpen && hostShowsSelection()) {
    // No strip below the grid to point at: the host's panel already has them.
    toast(tn(n, "{n} image selected", "{n} images selected"));
  } else if (n && n !== keptOnOpen) {
    toast(tn(n, "{n} image selected — in the strip below the grid",
             "{n} images selected — in the strip below the grid"),
          !selTaught);
    selTaught = true;
  }
}

/* The markup of one photo card. `data-ar` comes from the payload, so the box has its
   final shape before the image arrives. */
function cardHtml(f) {
  const ar = (f.w && f.h) ? (f.w / f.h) : 0;
  return '<div class="shot" data-ar="' + (ar || "") + '">'
    + '<img src="' + esc(f.large) + '" alt="" draggable="false">'
    + (f.id === starredHere() ? '<span class="vstar">' + star(16) + "</span>" : "")
    + '<div class="vinfo">' + metaHtml(f) + "</div>"
    + '<span class="vstamp keep">' + ICON.keepBig + "</span>"
    + '<span class="vstamp pass">' + ICON.passBig + "</span>"
    + '<span class="vstamp sup">' + ICON.supBig + "</span>"
    + "</div>";
}

// Without dimensions in the payload a card starts at DEFAULT_AR; the loaded file
// corrects it.
function fixRatio(img) {
  const shot = img.parentNode;
  if (shot && !parseFloat(shot.dataset.ar) && img.naturalWidth && img.naturalHeight) {
    shot.dataset.ar = img.naturalWidth / img.naturalHeight;
  }
  sizeCards();
}

/* A stamp's transform at strength r (0..1): a fixed tilt, and a scale that grows
   from two thirds to a little over full size as the throw commits. */
function markTf(kind, r) {
  const sc = (0.66 + r * 0.46).toFixed(3);
  if (kind === "keep") return "rotate(-18deg) scale(" + sc + ")";
  if (kind === "pass") return "rotate(18deg) scale(" + sc + ")";
  return "translateX(-50%) rotate(-8deg) scale(" + sc + ")";
}

/* A button press throws the same stamp the gesture would. */
function flashMark(kind) {
  if (REDUCED) return;
  const m = viewerEl.querySelector(".vcard.top .vstamp." + kind);
  if (!m) return;
  m.classList.remove("flash");
  void m.offsetWidth;
  m.classList.add("flash");
  setTimeout(() => m.classList.remove("flash"), 520);
}

function paintDeck() {
  saveUi();                                  // which photograph is on top
  const deck = viewerEl.querySelector(".vdeck");
  const total = DECK.length;
  deck.innerHTML = "";
  // The last swipe closes the deck; the liked photos wait under the grid.
  if (idx >= total) { closeViewer(); return; }
  const f = DECK[idx];
  // One card: the next one is built as this one leaves (revealNext).
  const el = document.createElement("div");
  el.className = "vcard top";
  el.innerHTML = cardHtml(f);
  const im = el.querySelector("img");
  im.addEventListener("error", () => el.classList.add("dead"));
  im.addEventListener("load", () => fixRatio(im));
  if (im.complete) fixRatio(im);
  deck.appendChild(el);
  dragify(el);
  requestAnimationFrame(sizeCards);
  watchDeck();
  prefetch(DECK, idx + 1);
  prefetch(DECK, idx + 2);
  // The sway, once per opening, on the card it opens on (openViewer resets `coached`);
  // none for a reader the coach is muted for.
  if (!coached && !REDUCED) {
    coached = true;
    const card = deck.querySelector(".vcard.top");
    if (card && !coachMuted()) {
      card.classList.add("sway");
      setTimeout(() => card.classList.remove("sway"), 2400);
    }
  }
  backdrop(f.thumb);
  paintMeta(f);
  paintChrome();
}

// Any real gesture means the reader got the idea: the sway stops before the drag
// writes its first inline transform, which the animation would override.
function stopCoaching() {
  const card = viewerEl.querySelector(".vcard.sway");
  if (card) card.classList.remove("sway");
}

// Inside the card, so the words travel with the photo: the credit, then the caption.
// A licence tag only when it is not the usual free one.
function metaHtml(f) {
  const lic = (f.license && !/^free$/i.test(f.license))
    ? '<span class="tag opt">' + esc(f.license) + "</span>" : "";
  return '<div class="by">' + "<span>" + creditHtml(f) + "</span>" + lic + "</div>"
    + (f.description ? '<p class="cap">' + esc(cap(f.description)) + "</p>" : "");
}

// One dash per photo: past dim, present lit, the rest faint. Rebuilt only when the
// count changes, and clicks are delegated from the bar, so a rebuild rewires nothing.
function paintSegs() {
  const segs = viewerEl.querySelector(".vsegs");
  const n = DECK.length;
  if (segs.childElementCount !== n) {
    let html = "";
    for (let k = 0; k < n; k++) {
      html += '<button type="button" data-seg="' + k + '" aria-label="'
        + esc(tx("Photo {k} of {n}", { k: k + 1, n: n })) + '"><i></i></button>';
    }
    segs.innerHTML = html;
  }
  let i = 0;
  for (const el of segs.children) {
    const bar = el.firstChild;
    bar.className = i < idx ? "done" : (i === idx ? "now" : "");
    el.setAttribute("aria-current", i === idx ? "true" : "false");
    i++;
  }
}

/* Jumping is not swiping: nothing is decided about the photo left, and the undo
   stack is dropped, since a rewind would walk back a step never taken. */
function goToPhoto(n) {
  if (n < 0 || n >= DECK.length || n === idx) return;
  idx = n;
  undoStack.length = 0;
  stopCoaching();
  paintDeck();
}

function paintMeta(f) {
  viewerEl.querySelector(".vbig.get").disabled = !f.pexafyUrl;
}

function paintChrome() {
  paintSegs();
  // Rewind steps back one photo, down to the first, whether or not anything was
  // swiped: a deck opened on the seventh photo can reach the first.
  viewerEl.querySelector(".vbig.rewind").disabled = idx <= 0;
  // ≈ is dead past the ceiling rather than answering with an apology; only where the
  // widget refines itself, since a host without a tool proxy keeps no trail.
  viewerEl.querySelector(".vbig.sup").disabled = canProxyTools() && refineExhausted();
  const keepBtn = viewerEl.querySelector(".vbig.keep");
  const held = isKept(idx);
  keepBtn.classList.toggle("on", held);
  keepBtn.title = held ? tx("Liked — swipe left or press skip to drop it") : tx("Like");
  paintCoach();
}

/* The next photo comes in, for real, while the current one leaves. */
function revealNext() {
  const deck = viewerEl.querySelector(".vdeck");
  const next = DECK[idx + 1];
  if (!next) return;
  const el = document.createElement("div");
  el.className = "vcard nx";
  el.innerHTML = cardHtml(next);
  const im = el.querySelector("img");
  im.addEventListener("error", () => el.classList.add("dead"));
  im.addEventListener("load", () => fixRatio(im));
  // Behind the card that is leaving.
  deck.insertBefore(el, deck.querySelector(".vcard.top"));
  sizeCards();
  requestAnimationFrame(() => el.classList.add("up"));
}

// A decision moves the deck on, and rewind takes it back: a swipe is quick to get
// wrong.
function decide(keep, dir) {
  if (idx >= DECK.length) return;
  stopCoaching();
  if (!dir) flashMark(keep ? "keep" : "pass");   // a drag already has its mark on screen
  revealNext();
  const top = viewerEl.querySelector(".vcard.top");
  undoStack.push({ i: idx, was: isKept(idx) });
  if (keep) setKept(idx, true);
  // A skip on a liked photo unlikes it, however the deck was opened; a skip on a
  // photo never liked decides nothing.
  else if (isKept(idx)) setKept(idx, false);
  const x = (dir || (keep ? 1 : -1)) * (viewerEl.clientWidth + 260);
  if (top) {
    top.classList.remove("drag");
    top.style.transform = "translate(" + x + "px," + (keep ? -40 : 40) + "px) rotate("
      + (keep ? 18 : -18) + "deg)";
    top.style.opacity = "0";
  }
  idx++;
  paintSegs();          // the dashes move WITH the card, not 300 ms behind it
  setTimeout(paintDeck, 300);
}
function rewind() {
  if (idx <= 0) return;
  // The photo goes back to how it was before this step, liked or not, and only when
  // this step changed it: a like given in the grid survives walking back past it.
  const h = undoStack.length && undoStack[undoStack.length - 1].i === idx - 1
    ? undoStack.pop() : null;
  idx--;
  paintSegs();
  if (h && h.was !== isKept(idx)) setKept(idx, h.was);
  stopCoaching();
  paintDeck();
}

/* ── ≈: more like this one ────────────────────────────────────────────────
   Not a like (the heart is that): a request for more photos like this one, with a
   blue burst over the deck and a ≈ badge left on the photo. `star`, `burstStar`,
   `markStar` and `superLike` are names from before the glyph became ≈. */
function burstStar() {
  if (REDUCED) return;
  const deck = viewerEl.querySelector(".vdeck");
  if (!deck) return;
  const b = document.createElement("div");
  b.className = "vburst";
  b.innerHTML = "<i></i>" + star(96);
  deck.appendChild(b);
  setTimeout(() => b.remove(), 780);
}

/* The badge on the photo more were asked from, written into the card on screen
   rather than through a repaint, so the photo does not flicker. */
function markStar() {
  const shot = viewerEl.querySelector(".vcard.top .shot");
  if (!shot || shot.querySelector(".vstar")) return;
  const b = document.createElement("span");
  b.className = "vstar";
  b.innerHTML = star(16);
  shot.appendChild(b);
}

function superLike() {
  const f = DECK[idx];
  if (!f || refining) return;
  stopCoaching();
  // The ceiling is checked before the burst: a celebration that then says no is
  // worse than a dead button.
  if (canProxyTools() && refineExhausted()) {
    toast(tx("Six refinements is the limit"));
    return;
  }
  burstStar();
  const btn = viewerEl.querySelector(".vbig.sup");
  if (btn && !REDUCED) {
    btn.classList.remove("fire");
    void btn.offsetWidth;                      // restart the animation on a second tap
    btn.classList.add("fire");
  }
  markStar();
  coachDid("sim");
  // It does not like the photo: readers ask for more like a photo they are unsure
  // of. The photo stays; a swipe up springs back here.
  const top = viewerEl.querySelector(".vcard.top");
  if (top) {
    top.classList.remove("drag");
    top.style.transform = "";
    top.style.opacity = "";
  }
  // Then the deck closes, since the answer is not in it: in the grid where the host
  // proxies tool calls, else in the thread (askSimilar).
  if (canProxyTools()) {
    /* The call goes out first and the deck closes once it lands: closing gives the
       screen back, which may reload the frame, and a call made from a frame that no
       longer exists paints nowhere. `refining`, set by refineFrom, stops a second tap. */
    setTimeout(() => {
      refineFrom(f).then(ok => { if (ok && deckOpen) closeViewer(); });
    }, REDUCED ? 0 : 430);
    return;
  }
  if (askSimilar()) setTimeout(closeViewer, REDUCED ? 0 : 430);
}

// Pointer drag: one gesture, four outcomes — right likes, left skips, up asks for
// more like this one, down closes. `touch-action: none` on the deck keeps the
// conversation from scrolling under the finger.
function dragify(el) {
  let sx = 0, sy = 0, dx = 0, dy = 0, on = false, id = null;
  const keepStamp = el.querySelector(".vstamp.keep");
  const passStamp = el.querySelector(".vstamp.pass");
  const supStamp  = el.querySelector(".vstamp.sup");
  const clamp = v => Math.max(0, Math.min(1, v));
  // The stamp fades in and grows under the thumb; it rides the card.
  const mark = (el, kind, r) => {
    if (!el) return;
    el.style.opacity = r;
    el.style.transform = markTf(kind, r);
  };
  // Vertical only when clearly more vertical than sideways, and the other way round,
  // or a diagonal flick would light two stamps at once.
  const vertical = () => Math.abs(dy) > Math.abs(dx) * 1.25;

  el.addEventListener("pointerdown", (e) => {
    if (e.button && e.button !== 0) return;
    on = true; id = e.pointerId; sx = e.clientX; sy = e.clientY; dx = dy = 0;
    dragMoved = 0;
    el.classList.add("drag");
    stopCoaching();
    try { el.setPointerCapture(id); } catch (_) {}
  });
  el.addEventListener("pointermove", (e) => {
    if (!on || e.pointerId !== id) return;
    dx = e.clientX - sx; dy = e.clientY - sy;
    dragMoved = Math.max(dragMoved, Math.abs(dx) + Math.abs(dy));
    el.style.transform = "translate(" + dx + "px," + dy + "px) rotate(" + (dx / 22) + "deg)";
    const up = vertical() && dy < 0;
    mark(keepStamp, "keep", up ? 0 : clamp(dx / 90));
    mark(passStamp, "pass", up ? 0 : clamp(-dx / 90));
    mark(supStamp, "sup", up ? clamp(-dy / 90) : 0);
  });
  const end = (e) => {
    if (!on || (e.pointerId !== undefined && e.pointerId !== id)) return;
    on = false;
    el.classList.remove("drag");
    try { el.releasePointerCapture(id); } catch (_) {}
    const w = viewerEl.clientWidth || 320;
    const gate = Math.min(130, w * 0.26);
    if (vertical() && Math.abs(dy) > 110) {
      if (dy < 0) superLike(); else closeViewer();
      return;
    }
    if (Math.abs(dx) > gate) { decide(dx > 0, dx > 0 ? 1 : -1); return; }
    el.style.transform = "";
    for (const m of [keepStamp, passStamp, supStamp]) {
      if (m) { m.style.opacity = 0; m.style.transform = ""; }
    }
  };

  el.addEventListener("pointerup", end);
  el.addEventListener("pointercancel", end);
  // A lost capture (the pointer left the frame, the host took it back) is a release,
  // or the card would freeze mid-throw.
  el.addEventListener("lostpointercapture", end);
}

/* ── ≈ without a tool proxy ───────────────────────────────────────────────
   A host that proxies no tool call still lets the widget speak: a user turn asking
   for more like this photo, with its photo_id (a rank alone has been mistaken for
   one) and the words of the search, so the assistant stays on the subject. The tool
   is not named: that read as an instruction pasted into the thread. With no way to
   speak, the photo's page on Pexafy lists similar photos. */
function askSimilar() {
  const f = DECK[idx];
  if (!f) return false;
  const words = wordsOf(trail[step]);
  const text = tx("Find me more photos like this one")
    + (f.description ? ' (' + f.description + ')' : '')
    + ' — photo_id "' + f.id + '"'
    + (words ? tx(', from my search "{words}"', { words: words }) : '') + '.';
  if (speak(text)) { toast(tx("Asked for more like this one")); followTheAnswer(); return true; }
  if (f.pexafyUrl) { openLink(f.pexafyUrl + "#similar-photos", "similar"); return true; }
  toast(tx("This host cannot ask for similar photos"));
  return false;
}

/* ── Rendering a tool result ──────────────────────────────────────────────
   The grid is a preview inside a conversation: it opens on six tiles and grows by six,
   then four, to sixteen at most. Six ends on a full row at two columns and at three
   (the column count follows the frame's width alone), and the rest of the answer is
   one press away on Pexafy. */
const GRID_PAGES = [6, 6, 4];
// A page keeps no more photos than the grid can show.
const GRID_MAX = GRID_PAGES.reduce((a, b) => a + b, 0);

function gridFirst() { return GRID_PAGES[0]; }
function gridShownMax() { return GRID_MAX; }
/* How many the next press adds: the steps differ in size. */
function gridMore() {
  let at = 0;
  for (const n of GRID_PAGES) { at += n; if (shown < at) return at - shown; }
  return 0;
}

let shown = GRID_PAGES[0];

/* ── The coach: three first steps, one at a time ──────────────────────────
   Three things readers did not find on their own: a tile opens (tap), ≈ fetches more
   like it (sim), the heart tells the assistant which photos they want (like). One
   line names the next step, under the grid, or over the deck's verb when the deck is
   where it happens. A step counts wherever and in whatever order it is done; the
   steps and the cross are kept with the view state, so the coach never comes back
   once the reader has shown they know. */
const COACH_STEPS = ["tap", "sim", "like"];
let coach = { tap: false, sim: false, like: false, off: false };
let coachNudges = 0, coachNudgeTimer = null;
/* The closing line stays on the grid until its cross is pressed. When the last step
   was a like in the deck, it shows over that heart first, for a few seconds and
   without a cross: the grid is out of sight until the deck closes. */
const COACH_FINAL_DECK_MS = 3800;
let coachLast = "", coachDeckFinal = false, coachDeckFinalTimer = null;

function coachNext() {
  if (coach.off) return "";
  for (const s of COACH_STEPS) if (!coach[s]) return s;
  return "";
}
function coachState() {
  // `last` too: the closing line opens on "Liked!" only after a like, and a reloaded
  // frame must say the same.
  return { tap: coach.tap, sim: coach.sim, like: coach.like, off: coach.off, last: coachLast };
}
function coachFinished() {
  return !coach.off && !coachNext() && !!(coach.tap || coach.sim || coach.like);
}
/* A step done: the line moves on, or after the last one says what comes of it. */
function coachDid(what) {
  if (coach.off || coach[what]) return;
  coach[what] = true;
  if (!coachNext()) {
    coachLast = what;
    if (what === "like" && deckOpen) {
      coachDeckFinal = true;
      clearTimeout(coachDeckFinalTimer);
      coachDeckFinalTimer = setTimeout(() => {
        coachDeckFinalTimer = null; coachDeckFinal = false; paintCoach();
      }, COACH_FINAL_DECK_MS);
    }
  }
  paintCoach();
  saveUi();
}
function coachOff() {
  if (coach.off) return;
  coach.off = true;
  coachDeckFinal = false;
  clearTimeout(coachDeckFinalTimer); coachDeckFinalTimer = null;
  paintCoach();
  saveUi();
}
/* The line for the next step: on the grid, the control to reach; in the deck, the
   control under the thumb. One line at a phone's width; the heart's line says what a
   like does, not only where to press. */
const COACH_SAY = {
  tap:  { grid: "Tap a photo to open it" },
  sim:  { grid: "Open a photo and press {similar} for more like it",
          deck: "Press {similar} for more photos like this one" },
  like: { grid: "Tap {heart} to like a photo — the assistant can then use it",
          deck: "Tap {heart} or swipe right to like it — the assistant will know" },
};
/* The closing line: the next move, not a receipt. It does not open on "Liked!" when
   the last step was not a like. */
const COACH_DONE_LIKED = "Liked! Now ask the assistant to use it in your project";
const COACH_DONE_OTHER = "All set — ask the assistant to use the photos you liked";
/* The lines that name the assistant, by its name where the grid knows it, as the line
   above the grid and the liked strip's title do (assistantName). */
const COACH_NAMED = new Map([
  ["Tap {heart} to like a photo — the assistant can then use it",
   "Tap {heart} to like a photo — {assistant} can then use it"],
  ["Tap {heart} or swipe right to like it — the assistant will know",
   "Tap {heart} or swipe right to like it — {assistant} will know"],
  ["Liked! Now ask the assistant to use it in your project",
   "Liked! Now ask {assistant} to use it in your project"],
  ["All set — ask the assistant to use the photos you liked",
   "All set — ask {assistant} to use the photos you liked"],
]);
/* A coach line in the reader's language, with the controls it names drawn in it. */
function coachHtml(src) {
  const who = assistantName();
  const named = who ? COACH_NAMED.get(src) : "";
  return esc(named ? tx(named, { assistant: who }) : tx(src))
    .replace("{similar}", "<b>" + ICON.starS + "</b>")
    .replace("{heart}", "<b>" + ICON.heartTip + "</b>");
}
function coachDoneSay() { return coachHtml(coachLast === "like" ? COACH_DONE_LIKED : COACH_DONE_OTHER); }

/* The tile whose heart the like step lights: the first one on screen that is neither
   the photo this page was asked from (it leads a refined grid, wearing ≈) nor already
   liked. On that photo, right after ≈, the lit heart read as "≈ liked it for me". */
function likeTarget() {
  const ref = starredHere();
  for (const c of gridEl.querySelectorAll(".card:not([hidden])")) {
    if (c.dataset.pid !== ref && !kept.has(c.dataset.pid)) return c;
  }
  return null;
}

function paintCoach() {
  const next = coachNext();
  const finished = coachFinished();
  const gridOn = !!(!deckOpen && PHOTOS.length && (finished || next));
  tipEl.hidden = !gridOn;
  tipEl.classList.toggle("done", finished);
  for (const el of gridEl.querySelectorAll(".coach")) el.classList.remove("coach");
  let nudge = null;
  if (gridOn) {
    let dots = "";
    COACH_STEPS.forEach((s, i) => {
      const done = !!(coach[s] || finished);
      if (i) dots += '<i class="sl' + ((coach[COACH_STEPS[i - 1]] || finished) ? " done" : "") + '"></i>';
      dots += '<i class="st' + (done ? " done" : (s === next ? " now" : "")) + '">'
        + (done ? ICON.tickS : (i + 1)) + "</i>";
    });
    const say = finished ? coachDoneSay() : coachHtml(COACH_SAY[next].grid);
    tipEl.innerHTML = '<span class="steps" aria-hidden="true">' + dots + "</span>"
      + '<span class="say">' + say + "</span>"
      + '<button class="tipx" type="button" aria-label="' + esc(tx("Got it")) + '">'
      + ICON.crossS + "</button>";
    tipEl.querySelector(".tipx").addEventListener("click", () => coachOff());
    // Light the control the sentence names: the first tile, or likeTarget's heart.
    if (next === "like") {
      const card = likeTarget();
      const pick = card && card.querySelector(".pick");
      if (pick) pick.classList.add("coach");
    } else if (next) {
      nudge = gridEl.querySelector(".card:not([hidden])");
      if (nudge) nudge.classList.add("coach");
    }
  }
  scheduleNudge(nudge);
  paintDeckCoach(next);
  // A step on screen is the coach shown: once per person (reportCoachShown).
  if (gridOn && next) reportCoachShown();
}

/* The first tile lifts, three times a breath apart, while the line says to tap it. */
function scheduleNudge(card) {
  clearTimeout(coachNudgeTimer); coachNudgeTimer = null;
  if (!card || REDUCED) return;
  coachNudges = 0;
  const lift = () => {
    coachNudgeTimer = null;
    if (deckOpen || coach.off || !card.isConnected) return;
    card.classList.remove("nudge"); void card.offsetWidth; card.classList.add("nudge");
    if (++coachNudges < 3) coachNudgeTimer = setTimeout(lift, 2600);
  };
  coachNudgeTimer = setTimeout(lift, 0);
}

/* The same line in the deck, over the verb it names (≈, or the heart), with the
   verb ringed; nothing for a step the deck has no control for, or over a disabled
   verb. After the like that finished the three steps, the closing line sits over the
   heart for a few seconds, unringed: nothing is being asked any more. */
let guideBtn = null;
function paintDeckCoach(next) {
  const guide = viewerEl.querySelector(".vguide");
  for (const b of viewerEl.querySelectorAll(".vbig.coach")) b.classList.remove("coach");
  const top = viewerEl.querySelector(".vcard.top");
  if (top) top.classList.remove("hintkeep");
  if (!guide) return;
  let say = "", btn = null;
  if (deckOpen && coachDeckFinal) {
    say = coachDoneSay();
    btn = viewerEl.querySelector(".vbig.keep");
  } else if (deckOpen && next && COACH_SAY[next] && COACH_SAY[next].deck) {
    say = coachHtml(COACH_SAY[next].deck);
    btn = viewerEl.querySelector(next === "like" ? ".vbig.keep" : ".vbig.sup");
    if (btn && !btn.disabled) {
      btn.classList.add("coach");
      if (next === "like" && top) top.classList.add("hintkeep");
    }
  }
  if (!btn || btn.disabled) {
    guide.hidden = true; guideBtn = null; viewerEl.classList.remove("guiding"); return;
  }
  viewerEl.classList.add("guiding");
  const changed = guide.dataset.say !== say;
  if (changed) { guide.innerHTML = say; guide.dataset.say = say; }
  guideBtn = btn;
  if (guide.hidden) {
    guide.hidden = false;                  // the entrance plays from display:none
  } else if (changed && !REDUCED) {
    guide.style.animation = "none"; void guide.offsetWidth; guide.style.animation = "";
  }
  placeGuide();
}

/* Over its verb, inside the frame: placed by `left` from the button's box (no
   transform, which the entrance would move), clamped 8px from the frame's edges with
   the arrow still over the button. Re-run on every resize of the deck (watchDeck):
   full screen on a phone changes the frame's width. */
function placeGuide() {
  const guide = viewerEl.querySelector(".vguide");
  if (!guide || guide.hidden || !guideBtn || !guideBtn.isConnected) return;
  const row = guide.parentElement.getBoundingClientRect();
  const b = guideBtn.getBoundingClientRect();
  const w = guide.offsetWidth;
  const vw = document.documentElement.clientWidth || window.innerWidth;
  const mid = b.left + b.width / 2;
  const left = Math.max(8, Math.min(mid - w / 2, vw - 8 - w));
  guide.style.left = Math.round(left - row.left) + "px";
  guide.style.setProperty("--ax", Math.round(mid - left) + "px");
}

function capShown() {
  let i = 0;
  for (const card of gridEl.children) { card.hidden = i >= shown; i++; }
  paintSlot();
  paintCoach();
  saveUi();
}
/* The one control under the grid: "See N more" while tiles are hidden, then the way
   out to Pexafy. */
function paintSlot() {
  // The wordmark leads to the same place, and carries the server's own label.
  brandEl.title = tx(footLabel);
  brandEl.setAttribute("aria-label", tx(footLabel));
  const left = Math.min(PHOTOS.length, gridShownMax()) - shown;
  moreEl.hidden = deckOpen || !PHOTOS.length;
  if (moreEl.hidden) { moreEl.innerHTML = ""; return; }
  if (left > 0) {
    moreEl.innerHTML = '<button class="moreb" type="button">'
      + esc(tn(Math.min(gridMore(), left), "See {n} more", "See {n} more")) + ICON.down + "</button>";
    moreEl.querySelector(".moreb").addEventListener("click", () => {
      shown += gridMore();
      capShown();
    });
    return;
  }
  // Where the button goes, and nothing about the grid (see origin.cta_for).
  moreEl.innerHTML = '<button class="brandcta" type="button"><span>' + esc(tx("Open in Pexafy")) + "</span>"
    + '<span class="arrow">' + ICON.right + "</span></button>";
  moreEl.querySelector(".brandcta").addEventListener("click", () => openLink(footUrl, "cta"));
}
/* ── Who is asking: the account button, top right of the head ─────────────
   One button on every answer, where sites put the account. Three states, all the
   server's (`_meta["pexafy/account"]` and `sc.budget`, see learnLinks):
     no notice, no account  the way in: one press starts the host's connect flow;
     a notice               a mark on the button, and the numbers behind it;
     signed in              a state, and a way back to the site.
   Every link goes through the host (`app.openLink`): an <a href> inside a sandboxed
   iframe navigates nowhere. */
let acctWho = null;      // who is asking, as the server sent it
let acctBudget = null;   // the answer's allowance block (budgetOf), or null
// When the wall was last seen, and how long the widget trusts it (see refineFrom).
let wallSeenAt = 0;
const WALL_GUARD_MS = 15000;
// The wall on screen, as the view keeps it (saveUi): { sig, wording } (paintWall).
let wallView = null;

function closePop() {
  acctPop.hidden = true;
  acctEl.setAttribute("aria-expanded", "false");
}

function openPop() {
  acctPop.hidden = false;
  acctEl.setAttribute("aria-expanded", "true");
  // The mark has been read: it stops beating here, not on a timer.
  acctDot.classList.remove("hint");
}

/* Connecting an account. The sign-in page creates an account but cannot attach it
   to this conversation, and nothing in the Apps SDK can. The connect tool can: it
   refuses with the host's authentication challenge, and the host runs its own
   connect flow. Called from the widget first (nothing is written in the thread), else
   asked of the model (one line in the thread), else the sign-in page. */
async function connectAccount(url) {
  try {
    if (canProxyTools()) {
      // Outside ChatGPT the call is answered 401 (anonymous.asks_to_sign_in), and the
      // host opens its own sign-in: say where the next step is.
      if (!window.openai) toast(tx("Sign in from the prompt your app opens"), true);
      await callServerTool("__TOOL_CONNECT__", {});
      watchForConnection();
      return;
    }
  } catch (e) {
    // The refusal is the challenge, and the host has acted on it by now: falling
    // through would ask twice.
    watchForConnection();
    return;
  }
  // speak() knows both ways a host takes a message, and which one ChatGPT prefers.
  if (speak(tx(CONNECT_ASK))) return;
  if (url) openLink(url, "signin");
}

/* Did the connection go through? It completes inside the host, and nothing tells the
   frame, so the button would look signed out until the next search. The connect tool
   with `check_only` answers without opening anything or spending a search (it reads
   the usage endpoint, which is exempt from the quota), in `structuredContent`:
   `{connected, budget}`. Once a second, for thirty seconds. No watch on that probe runs
   longer than PROBE_FOR_MAX_MS: a frame nobody is looking at stops calling soon. */
const PROBE_FOR_MAX_MS = 2 * 60 * 1000;
const CONNECT_WATCH_EVERY_MS = 1000;
const CONNECT_WATCH_FOR_MS = 30000;
let connectWatch = null;

function watchForConnection() {
  if (!canProxyTools() || connectWatch) return;
  const until = Date.now() + CONNECT_WATCH_FOR_MS;
  const tick = async () => {
    connectWatch = null;
    let connected = false;
    try {
      const res = await callServerTool("__TOOL_CONNECT__", { check_only: true });
      const sc = (res && res.structuredContent) || {};
      connected = sc.connected === true;
    } catch (e) {
      // The probe never raises: an error means the host refused the call, and asking
      // again would not change that.
      return;
    }
    if (connected) { markConnected(); return; }
    if (Date.now() < until) connectWatch = setTimeout(tick, CONNECT_WATCH_EVERY_MS);
  };
  connectWatch = setTimeout(tick, CONNECT_WATCH_EVERY_MS);
}

/* The account is attached: say so now, not at the next search. Only a positive
   probe calls this. The budget goes: the account's allowance replaces the daily one. */
function markConnected() {
  acctWho = { state: "signed_in", label: tx("Signed in"),
              home_url: (acctWho && acctWho.home_url) || PEXAFY_HOME };
  // Kept for the next answer too, should it come without `_meta` (learnLinks).
  acctMeta = { account: acctWho, budget: null };
  acctBudget = null;
  closePop();
  acctGlyph.innerHTML = ICON.user;
  acctEl.className = "acct in";
  acctDot.hidden = true;
  acctEl.setAttribute("aria-label", tx("Signed in"));
  acctEl.title = tx("Signed in");
  acctWrap.hidden = false;
  lastEl.hidden = true;
  toast(tx("Account connected — your next search uses it"));
}

/* Has the allowance come back — an account attached, the day over? The same probe,
   every ten seconds, for two minutes; after that the wall's "Try again" is still there,
   and a reload starts the watch over. */
const ALLOWANCE_WATCH_EVERY_MS = 10000;
const ALLOWANCE_WATCH_FOR_MS = PROBE_FOR_MAX_MS;
let allowanceWatch = null;

function watchForAllowance() {
  if (!canProxyTools() || allowanceWatch) return;
  const until = Date.now() + ALLOWANCE_WATCH_FOR_MS;
  const tick = async () => {
    allowanceWatch = null;
    let back = false;
    try {
      const res = await callServerTool("__TOOL_CONNECT__", { check_only: true });
      const sc = (res && res.structuredContent) || {};
      // Back when there is no budget block (nowhere near the end), or the one there
      // is no longer spent.
      const b = sc.budget;
      back = ("budget" in sc) && (!b || b.state !== "exhausted");
    } catch (e) {
      return;
    }
    if (back) { markAllowanceBack(); return; }
    if (Date.now() < until) allowanceWatch = setTimeout(tick, ALLOWANCE_WATCH_EVERY_MS);
  };
  allowanceWatch = setTimeout(tick, ALLOWANCE_WATCH_EVERY_MS);
}

/* The wall lifts: say so, and hand the next move back. "Search again" asks the model
   to run the search again, or runs it from the frame (searchAgain). */
function markAllowanceBack() {
  acctBudget = null;
  wallSeenAt = 0;
  closePop();
  acctEl.className = "acct" + (acctWho && acctWho.state === "signed_in" ? " in" : "");
  acctDot.hidden = true;
  lastEl.hidden = true;

  const card = wallEl.querySelector(".wallcard");
  if (!card) return;
  card.className = "wallcard back";
  card.innerHTML = "";
  const icon = document.createElement("span");
  icon.className = "wicon";
  icon.innerHTML = ICON.check;
  card.appendChild(icon);
  const t = document.createElement("p");
  t.className = "wt";
  t.textContent = tx("Your searches are back");
  card.appendChild(t);
  const d = document.createElement("p");
  d.className = "wd";
  d.textContent = tx("Ask again and the results will come.");
  card.appendChild(d);

  const go = document.createElement("button");
  go.className = "gobtn";
  go.type = "button";
  go.textContent = tx("Search again");
  go.addEventListener("click", () => {
    searchAgain();
  });
  card.appendChild(go);
}

/* What the model is asked when the widget cannot call the tool itself. An
   instruction: a question gets an answer in prose rather than the call. */
const CONNECT_ASK = "Connect my Pexafy account.";

/* What "Try again" and "Search again" ask the model: to run the last search again,
   which it finds in the conversation. */
const TRY_AGAIN_ASK = "Run my last Pexafy search again.";

/* "Search again" and "Try again". The model is asked where the host takes a message
   (speak). Where it does not — VS Code and Cursor run the frame's tool calls but carry
   no message to the chat — the frame runs the question behind this answer itself
   (ORIGIN, from `_meta`) and paints the answer as its own: the wall lifts, or comes
   back if the allowance is still spent. Never silent: a press that can do neither
   says so. */
let againBusy = false;
async function searchAgain() {
  if (speak(tx(TRY_AGAIN_ASK))) { toast(tx("Asked again")); return; }
  if (!ORIGIN || !canProxyTools()) { toast(tx("Ask again in the chat"), true); return; }
  if (againBusy) return;
  againBusy = true;
  toast(tx("Searching again…"));
  try {
    const res = await callServerTool(ORIGIN.tool, Object.assign({}, ORIGIN.args));
    try { learnLinks(res && (res._meta || res.meta)); } catch (e) {}
    render(res && res.structuredContent);
  } catch (e) {
    toast((e && e.message) ? e.message : tx("Could not search again"), true);
  } finally {
    againBusy = false;
  }
}

/* The panel's contents, rebuilt on every opening: `budget` may be on one answer and
   gone from the next. */
function fillPop() {
  const b = acctBudget;
  const who = acctWho;
  const signedIn = who ? who.state === "signed_in" : !!(b && b.signed_in);

  apTitle.textContent = (b && (b.title || b.short)) || (who && who.label) || "";
  // `message` when the panel's own wording did not come: the numbers, in a sentence.
  apDetail.textContent = (b && (b.detail || b.message)) || "";
  apDetail.hidden = !apDetail.textContent;

  const old = acctPop.querySelector(".gobtn");
  if (old) old.remove();
  // Where the button goes is the server's call: `account_url` is the sign-in form for
  // a visitor and the allowance page for an account. Absent, there is no button.
  const url = (b && b.account_url) || (!signedIn && who && who.sign_in_url) || "";
  const label = (b && b.cta_label) || (who && who.sign_in_label) || tx("Sign in");
  if (url) {
    const go = document.createElement("button");
    go.className = "gobtn";
    go.type = "button";
    go.textContent = label;
    go.addEventListener("click", () => {
      closePop();
      // A visitor is offered the host's connect flow; someone signed in, a page.
      if (signedIn) openLink(url, "account");
      else connectAccount(url);
    });
    acctPop.appendChild(go);
  }
}

/* An answer's allowance block, whole: its numbers and sentence (`sc.budget`, the half
   the model reads) with the panel's wording and button, which ride on `_meta`
   (learnLinks). An earlier server sent it whole in `sc.budget`, in the answers an older
   conversation still holds. */
function budgetOf(sc) {
  const b = sc && sc.budget;
  if (!b || typeof b !== "object") return null;
  return budgetWords(Object.assign({}, b, (acctMeta && acctMeta.budget) || {}));
}

/* The allowance's words in the reader's language, composed from its numbers as the
   server composes its English (budget.py: short, read, wall, _reset_phrase). English
   is the server's own wording, untouched, and so is a block without the numbers (an
   older server). */
function budgetWords(b) {
  if (!b || LANG === "en" || typeof b.remaining !== "number" || !b.scope) return b;
  const day = b.scope === "day";
  const left = Math.max(0, b.remaining);
  const short = left <= 0
    ? tx(day ? "Daily limit reached" : "Monthly limit reached")
    : (day ? tn(left, "{n} search left today", "{n} searches left today")
           : tn(left, "{n} search left this month", "{n} searches left this month"));
  const hours = Number(b.reset_hours) || 0;
  const reset = !day ? tx("It resets at the start of next month.")
    : (hours > 0 ? tn(hours, "It resets in about {n} hour.", "It resets in about {n} hours.")
                 : tx("It resets at midnight UTC."));
  const free = (b.free_account === false || b.signed_in) ? ""
    : tx(day ? "A free Pexafy account lifts this daily limit."
             : "A free Pexafy account lifts this monthly limit.");
  let title = short, detail;
  if (b.wall) {
    title = b.signed_in ? tx("This month's searches are used up")
      : tx(day ? "Today's free searches are used up" : "The free searches are used up");
    detail = free ? reset + " " + free : reset;
  } else {
    detail = free || reset;
  }
  const out = Object.assign({}, b, { short: short, title: title, detail: detail,
                                     message: title + ". " + detail });
  if (b.cta_label) out.cta_label = tx(b.cta_label);
  return out;
}

/* Who is asking, in the reader's language: the server's labels are English. */
function whoWords(w) {
  if (!w || typeof w !== "object") return w;
  const out = Object.assign({}, w);
  if (out.label) out.label = tx(out.label);
  if (out.sign_in_label) out.sign_in_label = tx(out.sign_in_label);
  return out;
}

function paintAccount(sc) {
  // `_meta` first; `sc.account` is where an earlier server put it.
  acctWho = whoWords((acctMeta && acctMeta.account) || (sc && sc.account) || null);
  acctBudget = budgetOf(sc);
  closePop();

  const b = acctBudget;
  if (!acctWho && !b) { acctWrap.hidden = true; return; }
  const signedIn = acctWho ? acctWho.state === "signed_in" : !!(b && b.signed_in);
  const url = signedIn ? "" : ((b && b.account_url) || (acctWho && acctWho.sign_in_url) || "");
  // No notice and nowhere to send anyone: no button, rather than one doing nothing.
  if (!signedIn && !url && !b) { acctWrap.hidden = true; return; }

  acctGlyph.innerHTML = ICON.user;
  const spent = !!b && b.state === "exhausted";
  // Stamped on arrival, so the guard measures the age of what is known.
  if (spent) wallSeenAt = Date.now();
  // The budget's state wins over "signed in", one colour per answer: `.warn` and
  // `.spent` come after `.in` in the sheet.
  acctEl.className = "acct" + (signedIn ? " in" : "")
                   + (b ? (spent ? " spent" : " warn") : "");

  const hadDot = !acctDot.hidden;
  acctDot.hidden = !b;
  // Three beats when the mark appears, not when a repaint keeps it.
  acctDot.classList.toggle("hint", !!b && !hadDot);

  acctEl.setAttribute("aria-label",
    [(b && (b.title || b.short)) || "", (acctWho && acctWho.label) || ""]
      .filter(Boolean).join(" — ") || tx("Account"));
  // The tooltip says what a press does.
  acctEl.title = (b && b.message) || (acctWho && acctWho.label) || "";
  acctWrap.hidden = false;
}

/* One press. With a notice (a count running down, or the wall) it opens the panel,
   where the numbers and the one button are; without one it goes straight through:
   the connect flow for a visitor, the site for someone signed in. */
acctEl.addEventListener("click", (e) => {
  e.stopPropagation();
  if (!acctPop.hidden) { closePop(); return; }
  // Under the wall the panel would repeat the wall: straight to the flow instead.
  if (acctBudget && wallEl.hidden) { fillPop(); openPop(); return; }
  const signedIn = acctWho && acctWho.state === "signed_in";
  if (signedIn) {
    openLink((acctWho && acctWho.home_url) || PEXAFY_HOME, "home");
    return;
  }
  // On every answer, so an account can be attached before the allowance runs out.
  connectAccount((acctWho && acctWho.sign_in_url) || "");
});

document.addEventListener("click", (e) => {
  if (!acctPop.hidden && !acctPop.contains(e.target)) closePop();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !acctPop.hidden) { closePop(); acctEl.focus(); }
});

/* ── The wall ─────────────────────────────────────────────────────────────
   A spent allowance comes back as an empty answer with `budget.state ===
   "exhausted"` (server._wall_as_grid) and is drawn here, under the same head: a tool
   error would draw no widget at all. False when the answer is not a wall, so render()
   says "no images" instead: nothing matched and none left are different things. */
function paintWall(sc) {
  const b = budgetOf(sc);
  if (!b || b.state !== "exhausted") return false;

  const signedIn = !!b.signed_in;
  const url = b.account_url || (!signedIn && acctWho && acctWho.sign_in_url) || "";
  wallEl.innerHTML = "";

  // Shapes, not photos: nothing was searched.
  const back = document.createElement("div");
  back.className = "wallblur";
  back.setAttribute("aria-hidden", "true");
  // gridFirst() cells: the wall is the size of a first page of results.
  for (let i = 0; i < gridFirst(); i++) back.appendChild(document.createElement("i"));
  wallEl.appendChild(back);

  const card = document.createElement("div");
  card.className = "wallcard" + (signedIn ? " spent" : "");
  const icon = document.createElement("span");
  icon.className = "wicon";
  icon.innerHTML = ICON.hourglass;
  card.appendChild(icon);

  const t = document.createElement("p");
  t.className = "wt";
  t.textContent = b.title || b.short || tx("The allowance is used up");
  card.appendChild(t);

  const d = document.createElement("p");
  d.className = "wd";
  d.textContent = b.detail || b.message || "";
  if (d.textContent) card.appendChild(d);

  if (url) {
    const go = document.createElement("button");
    go.className = "gobtn";
    go.type = "button";
    go.textContent = b.cta_label || tx("Sign in");
    go.addEventListener("click", () => {
      if (signedIn) openLink(url, "wall");
      else connectAccount(url);
    });
    card.appendChild(go);
  }
  // A way out: ask the model to search again, or run the search from the frame where
  // the host carries no message (searchAgain). Under the button, as the second answer.
  const again = document.createElement("button");
  again.className = "walllink";
  again.type = "button";
  again.textContent = tx("Try again");
  again.addEventListener("click", () => {
    searchAgain();
  });
  card.appendChild(again);

  wallEl.appendChild(card);
  // The allowance comes back (an account attached, the day over), and nothing tells
  // the frame: it goes and looks, for a while (watchForAllowance).
  watchForAllowance();

  // The head stays: same wordmark, same account button.
  statusEl.hidden = true;
  headEl.hidden = false;
  noteEl.hidden = true;
  gridEl.hidden = true;
  moreEl.hidden = true;
  selEl.hidden = true;
  trailEl.hidden = true;
  wallEl.hidden = false;
  /* A frame reloaded without this answer's `_meta` draws the same wall from the view
     (takeBack): who is asking (saveUi writes it), and the wall's own wording, kept under
     the wall's signature. At once: nothing says when the host reloads the frame. */
  const sig = wallSig(sc);
  wallView = sig ? { sig: sig, wording: (acctMeta && acctMeta.budget) || null } : null;
  saveUi(true);
  return true;
}

/* The strip over the grid on the last searches of an allowance, from the same `budget`
   block as the account button, so the two numbers agree. */
function paintLastCall(sc) {
  const b = budgetOf(sc);
  if (!b || b.signed_in) { lastEl.hidden = true; lastEl.innerHTML = ""; return; }
  // Two searches or fewer: until then the button's mark is enough.
  const left = typeof b.remaining === "number" ? b.remaining : null;
  if (left === null || left > 2) { lastEl.hidden = true; lastEl.innerHTML = ""; return; }

  lastEl.innerHTML = "";
  const icon = document.createElement("span");
  icon.className = "lc-icon";
  icon.innerHTML = ICON.hourglass;
  lastEl.appendChild(icon);

  const text = document.createElement("div");
  text.className = "lc-text";
  const t = document.createElement("p");
  t.className = "lc-t";
  t.textContent = left <= 0 ? tx("That was the last free search today")
                            : (b.short || tx("Nearly out of free searches"));
  text.appendChild(t);
  const d = document.createElement("p");
  d.className = "lc-d";
  d.textContent = b.detail || "";
  if (d.textContent) text.appendChild(d);
  lastEl.appendChild(text);

  const url = b.account_url || (acctWho && acctWho.sign_in_url) || "";
  // The account block's own label when the panel's wording did not come.
  const label = b.cta_label || (acctWho && acctWho.sign_in_label) || "";
  if (label) {
    const go = document.createElement("button");
    go.className = "gobtn";
    go.type = "button";
    go.textContent = label;
    go.addEventListener("click", () => connectAccount(url));
    lastEl.appendChild(go);
  }
  lastEl.hidden = false;
}

/* Which answer a payload is, by its photo ids, read before anything is built from it
   (resultSig names the answer a view was written over). */
function payloadSig(sc) {
  const raw = (sc && sc.data) || [];
  if (!Array.isArray(raw) || !raw.length) return "";
  return raw.map(p => (p && p.photo_id) || "").join(",");
}

/* Which wall an answer is. A wall has no photographs to be known by, so its notice —
   or, from a server that sent none, its budget's sentence — hashed (FNV-1a). "" for an
   answer that is not a wall. */
function wallSig(sc) {
  const b = sc && sc.budget;
  if (!b || typeof b !== "object" || b.state !== "exhausted" || payloadSig(sc)) return "";
  const text = String(sc.notice || b.message || "");
  if (!text) return "";
  let h = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) h = Math.imul(h ^ text.charCodeAt(i), 0x01000193);
  return "w" + (h >>> 0).toString(36);
}

/* A tool result from the host: a new question in the conversation, so the trail
   starts over at its anchor and the old likes go. */
function render(sc) {
  // A host that runs the coach itself (embedOpt) mutes this one, and so does the
  // server once the caller has met it (serverMutesCoach).
  if (coachMuted()) coach.off = true;
  /* A result the widget already has is not a new question: a host may hand the
     answer to the widget's own call back as a tool result, and the reset below would
     empty the trail and the likes. Recognised by its photo ids; the account corner
     still takes it, since the call did spend a search. */
  const psig = payloadSig(sc);
  if (psig && trail.some(p => p.psigs.indexOf(psig) !== -1)) {
    paintAccount(sc);
    paintLastCall(sc);
    return;
  }
  // A new answer: the view keeps a wall only while one is on screen (paintWall).
  wallView = null;
  // Before the photos: buildPhotos takes the answer's link from it, and fields()
  // writes each page URL on the site it names.
  takeBack(sc);
  const built = buildPhotos(sc, null);
  paintAccount(sc);
  if (!built.photos.length) {
    // Empty because the allowance is gone (the wall), or because nothing matched.
    if (paintWall(sc)) return;
    say(tx("No images to display."));
    return;
  }
  wallEl.hidden = true;
  paintLastCall(sc);
  trail.length = 0;
  trail.push(newPage(null, built, sc));
  step = 0;
  kept.clear();
  keptPhotos.clear();
  selReordered = false;
  showStep();
  restoreUi();
  // Muted by the server: written with the view at once, so a frame the host reloads
  // without this answer's `_meta` does not coach again.
  if (serverMutesCoach()) saveUi();
}

/* Paint the page on screen, whether it came from a tool result or from a call the
   widget made. */
function paintGrid() {
  const starred = starredHere();
  gridEl.innerHTML = "";
  PHOTOS.forEach((f, at) => {
    const on = kept.has(f.id);
    const card = document.createElement("div");
    card.className = on ? "card on" : "card";
    card.dataset.pid = f.id;                 // how the shortlist finds its tile again
    card.tabIndex = 0;
    card.setAttribute("role", "button");
    card.setAttribute("aria-label", f.author ? tx("Photo by {author}", { author: f.author }) : tx("Photo"));
    card.innerHTML =
      '<img loading="lazy" alt="" draggable="false" src="' + esc(f.thumb) + '">'
      + '<span class="veil"></span>'
      // The photo a refinement was asked from wears ≈ where the source chip goes.
      + (f.id === starred
          ? '<span class="cstar">' + ICON.starS + "</span>"
          : (f.source ? '<span class="src">' + esc(f.source) + "</span>" : ""))
      + (f.author ? '<div class="by">' + esc(f.author) + "</div>" : "")
      // The shape, only when the payload gives it: no guessed mark.
      + (SHAPE_BOX[f.orientation]
          ? '<span class="shape" title="' + esc(shapeName(f.orientation))
            + '">' + shapeGlyph(f.orientation, 15, 2.1) + "</span>"
          : "")
      + '<button class="pick" type="button" aria-label="' + esc(tx("Like this photo")) + '">'
      + (on ? ICON.check : ICON.heartS) + "</button>";
    // A thumbnail link lasts at least 30 days by default (previews.THUMB_TTL): past
    // that, or on any other failure, the tile becomes a plain placeholder.
    card.querySelector("img").addEventListener("error", () => card.classList.add("dead"));
    card.querySelector(".pick").addEventListener("click", (e) => {
      e.stopPropagation();
      // By the photo, not the index: setKept() reads DECK, which may be the liked
      // photos while a review deck is open.
      setKeptPhoto(f, !kept.has(f.id));
    });
    card.addEventListener("click", () => { try { openViewer(at); } catch (e) {} });
    card.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openViewer(at); }
    });
    gridEl.appendChild(card);
  });
  paintSelBlock();
  prefetch(PHOTOS, 0);
  statusEl.hidden = true;
  headEl.hidden = false;
  gridEl.hidden = false;
  paintNote();
  capShown();
}

/* ── Turning the page with the thumb ──────────────────────────────────────
   A drag across the grid moves between trail pages: left for the next, right for the
   one before. Only once the trail has two pages, and only once the movement is
   clearly sideways (`pan-y` plus a 1.4× bias), so scrolling through the widget still
   scrolls the conversation; at either end the grid rubber-bands. A drag that ends on
   a tile does not open it: the click is swallowed in the capture phase, judged on the
   furthest the gesture went. */
const PAGE_GATE = 64;
let gOn = false, gId = null, gx = 0, gy = 0, gMoved = 0, gLive = false;

gridEl.addEventListener("pointerdown", (e) => {
  if ((e.button && e.button !== 0) || refining || trail.length < 2) return;
  gOn = true; gId = e.pointerId; gx = e.clientX; gy = e.clientY;
  gMoved = 0; gLive = false;
});
gridEl.addEventListener("pointermove", (e) => {
  if (!gOn || e.pointerId !== gId) return;
  const dx = e.clientX - gx, dy = e.clientY - gy;
  gMoved = Math.max(gMoved, Math.abs(dx) + Math.abs(dy));
  if (!gLive) {
    // Not ours until it is clearly sideways: until then the reader is scrolling.
    if (Math.abs(dx) < 14 || Math.abs(dx) < Math.abs(dy) * 1.4) return;
    gLive = true;
    gridEl.style.transition = "none";
    try { gridEl.setPointerCapture(gId); } catch (_) {}
  }
  const travel = stepExists(step + (dx < 0 ? 1 : -1)) ? dx * 0.42 : dx * 0.11;
  gridEl.style.transform = "translateX(" + travel.toFixed(1) + "px) rotate("
    + (travel / 300).toFixed(3) + "deg)";
});
const gEnd = (e) => {
  if (!gOn || (e.pointerId !== undefined && e.pointerId !== gId)) return;
  gOn = false;
  try { gridEl.releasePointerCapture(gId); } catch (_) {}
  if (!gLive) return;
  gLive = false;
  const dx = e.clientX - gx;
  const dir = dx < 0 ? 1 : -1;
  if (Math.abs(dx) > PAGE_GATE && stepExists(step + dir)) goToStep(step + dir);
  else snapGrid();
};
gridEl.addEventListener("pointerup", gEnd);
gridEl.addEventListener("pointercancel", gEnd);
gridEl.addEventListener("lostpointercapture", gEnd);
// Capture phase, so it runs before the tile's own handler.
gridEl.addEventListener("click", (e) => {
  if (gMoved > 8) { e.stopPropagation(); e.preventDefault(); }
}, true);

/* ── Wiring ───────────────────────────────────────────────────────────────── */
viewerEl.querySelector(".vbig.rewind").innerHTML = ICON.rewind;
viewerEl.querySelector(".vbig.pass").innerHTML = ICON.cross;
viewerEl.querySelector(".vbig.sup").innerHTML = ICON.star;
viewerEl.querySelector(".vbig.keep").innerHTML = ICON.heart;
viewerEl.querySelector(".vbig.get").innerHTML = ICON.get;
viewerEl.querySelector(".vside.back").innerHTML = ICON.back;
/* The texts written into the page before the language was known, again in it: the
   status line while nothing else was said, the deck's legends (round glyphs come with
   none), the dead preview's label. Run again whenever the language changes. */
function localizeStatic() {
  if (!statusSaid) statusEl.textContent = tx("Pexafy viewer — loading…");
  try { document.title = tx("Pexafy results"); } catch (e) {}
  const legend = (sel, label) => {
    const el = viewerEl.querySelector(sel);
    if (el) { el.title = label; el.setAttribute("aria-label", label); }
  };
  legend(".vbig.rewind", tx("Back to the previous photo"));
  legend(".vbig.pass", tx("Skip"));
  legend(".vbig.sup", tx("More photos like this one"));
  legend(".vbig.keep", tx("Like"));
  legend(".vbig.get", tx("Download it from Pexafy"));
  legend(".vside.back", tx("Back to the grid"));
  try {
    document.documentElement.style.setProperty("--t-dead", JSON.stringify(tx("Preview unavailable")));
  } catch (e) {}
}
localizeStatic();

viewerEl.querySelector(".vside.back").addEventListener("click", closeViewer);
// The wordmark leads to the same page as the button under the grid.
brandEl.addEventListener("click", () => openLink(footUrl, "wordmark"));
// A click on empty ground closes the deck; the photo, the controls and the top bar
// do not.
viewerEl.addEventListener("click", (e) => {
  // A throw that springs back fires a click on release: a gesture, not a tap.
  if (dragMoved > 6) return;
  const t = e.target;
  if (t && t.closest && t.closest(".vctl, .vtop")) return;
  // Where, not what: the card holds the pointer capture and spans the whole deck, so
  // the event names it for a tap anywhere. The photo's own rectangle decides.
  const shot = viewerEl.querySelector(".vcard.top .shot");
  if (shot) {
    const r = shot.getBoundingClientRect();
    if (e.clientX >= r.left && e.clientX <= r.right
        && e.clientY >= r.top && e.clientY <= r.bottom) return;
  }
  closeViewer();
});
viewerEl.querySelector(".vsegs").addEventListener("click", (e) => {
  const b = e.target && e.target.closest && e.target.closest("[data-seg]");
  if (b) goToPhoto(Number(b.getAttribute("data-seg")));
});
viewerEl.querySelector(".vbig.rewind").addEventListener("click", rewind);
viewerEl.querySelector(".vbig.pass").addEventListener("click", () => decide(false));
viewerEl.querySelector(".vbig.sup").addEventListener("click", superLike);
viewerEl.querySelector(".vbig.keep").addEventListener("click", () => decide(true));
// The one action that leaves the widget: the sandbox cannot hand over a file, so it
// opens the photo's page on Pexafy, where the download is.
viewerEl.querySelector(".vbig.get").addEventListener("click", () => {
  const f = DECK[idx];
  if (f) openLink(f.pexafyUrl || footUrl, "download");
});

document.addEventListener("keydown", (e) => {
  if (!deckOpen) return;
  if (e.key === "Escape") { closeViewer(); return; }
  // Backspace and ArrowDown step back one photo.
  if (e.key === "Backspace" || e.key === "ArrowDown") { e.preventDefault(); rewind(); return; }
  if (idx >= DECK.length) return;
  if (e.key === "ArrowRight") { e.preventDefault(); decide(true); }
  else if (e.key === "ArrowLeft") { e.preventDefault(); decide(false); }
  else if (e.key === "ArrowUp") { e.preventDefault(); superLike(); }
});

/* The `_meta` of a tool result. Only ours counts (ourMeta): a host may hand the frame a
   result without it, or with an empty one or only its own keys — a reloaded frame, or a
   host that does not relay it — and ChatGPT keeps its own copy in
   `window.openai.toolResponseMetadata`. That copy describes the call that opened this
   frame, so it stands in for the first result only. */
let resultsSeen = 0;
function resultMeta(params) {
  const first = resultsSeen++ === 0;
  const meta = params ? (ourMeta(params._meta) || ourMeta(params.meta)) : null;
  if (meta || !first) return meta;
  return heldMeta();
}

/* ChatGPT's copy. The reference gives it two shapes: the `_meta` itself, and an object
   that "includes `status`, `call_tool_result`, and `mcp_tool_result`, preserving the full
   MCP result envelope, including hidden `_meta`". Both are read, envelope first: the
   first place that holds a key of ours. */
function heldMeta() {
  try {
    const held = window.openai && window.openai.toolResponseMetadata;
    if (!held || typeof held !== "object") return null;
    const metaOf = (r) => (r && typeof r === "object")
      ? (ourMeta(r._meta) || ourMeta(r.meta)) : null;
    return metaOf(held) || metaOf(held.mcp_tool_result) || metaOf(held.call_tool_result)
      || ourMeta(held);
  } catch (e) { return null; }
}

app.ontoolresult = (params) => {
  // The links first: render() reads them through fields().
  try { learnLinks(resultMeta(params)); } catch (e) {}
  try { render(params && params.structuredContent); }
  catch (e) { say(tx("Could not render the results.") + " " + (e && e.message ? e.message : ""), true); }
};
app.onhostcontextchanged = (ctx) => {
  // A notification carries only what changed. Without a theme, applying it would set
  // data-theme="undefined" and drop the dark theme the host pushed earlier.
  if (ctx && ctx.theme) { try { applyDocumentTheme(ctx.theme); } catch (e) {} }
  if (ctx && ctx.locale) { try { if (readLang()) relocalize(); } catch (e) {} }
  try { syncDisplayMode(ctx); } catch (e) {}
};
app.onerror = (e) => { try { console.error(e); } catch (_) {} };

/* The try-it page's panel has a cross on each liked photo, and the like lives here: a
   photo dropped there is dropped here too, or the next change would put it back. A
   notification of ours, not of the Apps protocol (the SDK ignores unknown methods),
   taken only from the parent frame. */
window.addEventListener("message", (e) => {
  if (e.source !== window.parent) return;
  const m = e.data;
  if (!m || m.jsonrpc !== "2.0" || m.method !== "pexafy/unlike") return;
  const f = keptPhotos.get(String((m.params && m.params.photo_id) || ""));
  if (f) setKeptPhoto(f, false);
});

try {
  await app.connect();
  try { const c = app.getHostContext(); if (c && c.theme) applyDocumentTheme(c.theme); } catch (e) {}
  // The host's language, now that it has said it (hostContext.locale).
  try { if (readLang()) relocalize(); } catch (e) {}
  try { paintBrand(); } catch (e) {}
  // Connected, and no result yet.
  if (gridEl.hidden && !statusSaid) {
    say(tx("Connected — waiting for results…"));
  }
} catch (e) {
  say(tx("Could not connect to the host.") + " " + (e && e.message ? e.message : e), true);
}
""".replace("__EXT_APPS_CDN__", _EXT_APPS_CDN)


# The viewer's chrome is static markup: it never changes between photos, only its
# contents do, so it is written once here rather than rebuilt on every swipe.
_VIEWER_HTML = """
<div id="viewer" hidden>
  <div class="vback"><i></i><i></i></div>
  <div class="vshell">
    <div class="vtop">
      <span class="vsegs"></span>
    </div>
    <div class="vbody">
      <div class="vdeck"></div>
      <div class="vctl">
        <button class="vside back" type="button" aria-label="Back to the grid"></button>
        <div class="vverbs">
          <div class="vguide" hidden></div>
          <button class="vbig rewind" type="button" aria-label="Back to the previous photo"></button>
          <button class="vbig pass" type="button" aria-label="Skip"></button>
          <button class="vbig sup" type="button" aria-label="More photos like this one"></button>
          <button class="vbig keep" type="button" aria-label="Like"></button>
          <button class="vbig get" type="button" aria-label="Download it from Pexafy"></button>
        </div>
      </div>
    </div>
  </div>
</div>
"""


def _build_html() -> str:
    sdk_script = (
        f'<script type="module">{_INLINE_SDK}</script>\n' if SDK_INLINED else ""
    )
    return (
        "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<meta name="color-scheme" content="light dark">\n'
        "<title>Pexafy results</title>\n"
        f"<style>{_STYLE}</style>\n</head>\n<body>\n"
        '<div id="status">Pexafy viewer — loading…</div>\n'
        '<div class="gridhead" id="gridhead" hidden>'
        '<button class="brandmark" type="button">Pexafy</button>'
        '<nav class="trail" id="trail" hidden></nav>'
        '<div class="headacts">'
        '<div class="shapewrap" id="shapewrap" hidden>'
        '<button class="shapeb" id="shapeb" type="button" aria-haspopup="menu" '
        'aria-expanded="false" aria-label="Filter by shape" title="Filter by shape">'
        '<span class="sbglyph" id="shapeglyph"></span></button>'
        '<div class="shapepop" id="shapepop" role="menu" hidden></div>'
        '</div>'
        '<button class="bugb" id="bugb" type="button"></button>'
        '<div class="acctwrap" id="acctwrap" hidden>'
        '<button class="acct" id="acct" type="button" aria-haspopup="dialog" '
        'aria-expanded="false"><span class="aglyph" id="acctglyph"></span>'
        '<span class="dot" id="acctdot" hidden></span></button>'
        '<div class="acctpop" id="acctpop" role="dialog" hidden>'
        '<p class="ap-t" id="apTitle"></p><p class="ap-d" id="apDetail"></p>'
        '</div></div></div>'
        '<p class="sharenote" id="sharenote" hidden><span id="sharenoteText"></span>'
        '<button class="sharex" id="sharenoteX" type="button"></button></p>'
        '</div>\n'
        '<div class="grid" id="grid" hidden></div>\n'
        '<p class="gridtip" id="gridtip" hidden></p>\n'
        '<div class="wall" id="wall" hidden></div>\n'
        '<div class="lastcall" id="lastcall" hidden></div>\n'
        '<div class="gridmore" id="gridmore" hidden></div>\n'
        '<section class="selblock" id="selblock" hidden></section>\n'
        f"{_VIEWER_HTML}"
        '<div id="toast" hidden></div>\n'
        '<div id="end" aria-hidden="true"></div>\n'
        f"{sdk_script}"
        f'<script type="module">{_WIDGET_JS}</script>\n'
        "</body>\n</html>\n"
    )


def _with_tool_names(html: str) -> str:
    """Put the current tool names, argument names and version into the frame's JS.

    The widget calls tools by name, exactly as a model does, so the names come from the
    registry the server builds its tools from, never from a copy in the JavaScript. The
    version it declares to the host is the package's, and the thumbnails' base is the
    one the server signs its previews under (previews.THUMB_BASE_URL).
    """
    from . import previews

    return (
        html
        .replace("__THUMB_BASE__", previews.THUMB_BASE_URL)
        .replace("__TOOL_BY_IMAGE__", tooling.PUBLIC_TOOL_NAMES["search_photos_by_image"])
        .replace("__TOOL_CONNECT__", tooling.PUBLIC_TOOL_NAMES["connect_account"])
        .replace("__ARG_SENTENCE__", tooling.PUBLIC_QUERY_PARAM)
        .replace("__ARG_ORIENTATION__", tooling.PUBLIC_ORIENTATION_PARAM)
        .replace("__APP_VERSION__", __version__)
        .replace("__I18N__", widget_i18n.as_json())
    )


GRID_HTML = _with_tool_names(_build_html())


# The name drawn as the site's logo — `.site-logo .logo-text`'s gradient, violet to cyan —
# for the hosts that show neither the app's name nor its logo above the frame (VS Code,
# Cursor…). Not in GRID_HTML: ChatGPT shows its own, and OpenAI's guidelines forbid a
# second one and custom gradients (test_no_logo_and_no_custom_gradient); Claude shows its
# own too. hosts.LogoWhereTheHostDrawsNone adds it on the way out, and the frame's
# paintBrand puts the class on the name where the host is neither.
LOGO_CSS = (
    ".brandmark.logo { background: linear-gradient(135deg, var(--primary), #06b6d4);"
    " -webkit-background-clip: text; background-clip: text;"
    " -webkit-text-fill-color: transparent; }"
)


def with_logo(html: str) -> str:
    """The grid page with LOGO_CSS, for a host that draws no name above the frame."""
    return html.replace("</head>", f"<style>{LOGO_CSS}</style>\n</head>", 1)
