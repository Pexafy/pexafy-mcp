"""MCP App (UI) widget for the Pexafy search tools — an inline result grid.

claude.ai (web) does NOT render MCP `ImageContent` tool results inline — they only
appear in a collapsed tool panel. The only supported way to show a thumbnail grid
*inline in the chat* is the official MCP Apps extension (Anthropic, 2026-01): the
tool declares a `ui://` UI resource via `_meta.ui.resourceUri`, and the host renders
that HTML in a sandboxed iframe right in the conversation.

CRITICAL: claude.ai's app sandbox does NOT allow the iframe to fetch external scripts
at runtime (an `import` from a CDN fails → the App never initialises → the host never
gets a ready/size signal → the iframe stays 0px → totally blank). The official
ext-apps examples sidestep this by BUNDLING the SDK into a single self-contained HTML
(vite-plugin-singlefile). We do the same: the `@modelcontextprotocol/ext-apps`
browser bundle is vendored (ext_apps_bundle.js) and inlined here, its module exports
rebound onto `globalThis` so the widget script can use them with no network at all.

The widget reads the tool's `structuredContent.data` (each photo carries a
server-signed `preview_url`, see previews.inject_preview_urls) and paints a
responsive grid of thumbnails. Clicking a card opens an in-widget DETAIL panel
showing the photo's metadata (source, resolution, photographer, license, dominant
colour, orientation, date, caption) with buttons to open the full image or the
provider page via the host (`app.openLink`). Every metadata field is already present
in each photo of the tool result, so the panel needs no extra network call.
Thumbnails load from Pexafy's own signed thumb CDN (one CSP origin we control); the
HMAC secret never reaches the iframe.
"""
from __future__ import annotations

import base64
import re
from pathlib import Path

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


# Pexafy's brand font (Inter) vendored as woff2 and inlined as base64 @font-face, so
# the widget renders in the real Pexafy typeface with NO network fetch (the app
# sandbox blocks external font/style requests just like scripts). Two subsets mirror
# django_app/templates/base.html (latin + latin-ext) with the same unicode-ranges.
_FONT_FILES = [
    ("inter-latin.woff2",
     "U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+0304,U+0308,"
     "U+0329,U+2000-206F,U+20AC,U+2122,U+2191,U+2193,U+2212,U+2215,U+FEFF,U+FFFD"),
    ("inter-latin-ext.woff2",
     "U+0100-02BA,U+02BD-02C5,U+02C7-02CC,U+02CE-02D7,U+02DD-02FF,U+0304,U+0308,U+0329,"
     "U+1D00-1DBF,U+1E00-1E9F,U+1EF2-1EFF,U+2020,U+20A0-20AB,U+20AD-20C0,U+2113,"
     "U+2C60-2C7F,U+A720-A7FF"),
]


def _load_inline_font() -> str:
    rules = []
    for fname, urange in _FONT_FILES:
        path = Path(__file__).resolve().parent / "assets" / fname
        try:
            b64 = base64.b64encode(path.read_bytes()).decode("ascii")
        except OSError:
            continue
        rules.append(
            "@font-face{font-family:'Inter';font-style:normal;font-weight:400 700;"
            "font-display:swap;src:url(data:font/woff2;base64," + b64
            + ") format('woff2');unicode-range:" + urange + "}"
        )
    return "".join(rules)


_FONT_CSS = _load_inline_font()

# Resource CSP needs the thumb CDN for images; the SDK is inlined (no CDN) unless the
# vendored bundle is missing, in which case esm.sh is needed for the fallback import.
RESOURCE_EXTRA_DOMAINS: list[str] = [] if SDK_INLINED else [EXT_APPS_ORIGIN]


_STYLE = """
  /* Pexafy design tokens — mirror django_app/templates/base.html (light/dark). */
  :root { color-scheme: light dark;
    --fg: #18181b; --muted: #52525b; --surface: #ffffff; --surface-2: #f4f4f7;
    --border: rgba(0,0,0,.08); --primary: #7c3aed; --primary-hover: #6d28d9;
    --on-primary: #ffffff; --chip: rgba(0,0,0,.05); --primary-soft: rgba(124,58,237,.10);
    --r-card: 14px; --r-sheet: 22px; --r-btn: 11px;
    --shadow-card: 0 6px 18px rgba(0,0,0,.10); --shadow-sheet: 0 30px 70px rgba(0,0,0,.32);
    --font: 'Inter', system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif; }
  @media (prefers-color-scheme: dark) {
    :root { --fg: #e4e4e7; --muted: #a1a1aa; --surface: #18181b; --surface-2: #1f1f24;
      --border: rgba(255,255,255,.09); --primary: #8b5cf6; --primary-hover: #7c3aed;
      --chip: rgba(255,255,255,.08); --primary-soft: rgba(139,92,246,.16);
      --shadow-card: 0 6px 18px rgba(0,0,0,.4); --shadow-sheet: 0 30px 70px rgba(0,0,0,.6); } }

  * { box-sizing: border-box; }
  html, body { margin: 0; }
  body { min-height: 64px; background: transparent; color: var(--fg);
         font-family: var(--font); -webkit-font-smoothing: antialiased; }
  #status { padding: 16px 18px; font-size: 13px; line-height: 1.5; color: var(--muted); }
  #status.err { color: #dc2626; }

  /* ---- Result grid (≈4 per row) ---- */
  .grid { display: grid; gap: 12px; padding: 14px;
          grid-template-columns: repeat(auto-fill, minmax(144px, 1fr)); }
  .card { position: relative; aspect-ratio: 1 / 1; border-radius: var(--r-card); overflow: hidden;
          cursor: pointer; background: var(--surface-2); border: 1px solid var(--border);
          box-shadow: var(--shadow-card); transition: transform .16s ease, box-shadow .16s ease; }
  .card:hover { transform: translateY(-3px); box-shadow: 0 12px 28px rgba(0,0,0,.18); }
  .card img { width: 100%; height: 100%; object-fit: cover; display: block;
              transition: transform .3s ease; }
  .card:hover img { transform: scale(1.05); }
  /* Rank — top-left, the user-facing handle ("find similar to #3"). Pexafy purple. */
  .card .rank { position: absolute; top: 6px; left: 6px; min-width: 18px; height: 17px;
                padding: 0 5px; display: inline-flex; align-items: center; justify-content: center;
                font-size: 9.5px; font-weight: 700; color: var(--on-primary); border-radius: 999px;
                background: var(--primary); box-shadow: 0 1px 5px rgba(124,58,237,.45); }
  /* Source — top-right, frosted. */
  .card .src { position: absolute; top: 6px; right: 6px; padding: 2px 7px; font-size: 9px;
               font-weight: 600; letter-spacing: .2px; color: #fff; border-radius: 999px;
               background: rgba(0,0,0,.42); backdrop-filter: blur(6px);
               -webkit-backdrop-filter: blur(6px); }
  /* Photographer credit — bottom, on a soft gradient. */
  .card .credit-bar { position: absolute; inset: auto 0 0 0; padding: 16px 10px 8px;
                      background: linear-gradient(transparent, rgba(0,0,0,.66));
                      font-size: 11px; font-weight: 600; letter-spacing: .2px; color: #fff;
                      white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  /* "By Pexafy" — ONCE, centred, at the foot of the whole widget. */
  .gridfoot[hidden] { display: none; }
  .gridfoot { padding: 4px 12px 16px; text-align: center; }
  .gridfoot .brand { font-size: 12px; font-weight: 600; letter-spacing: .3px; color: var(--muted);
                     cursor: pointer; transition: color .15s ease; }
  .gridfoot .brand:hover { color: var(--primary); }
  .brand .dot { display: inline-block; width: 7px; height: 7px; border-radius: 50%;
                background: var(--primary); margin-right: 5px; vertical-align: 0;
                box-shadow: 0 0 7px var(--primary); }

  /* ---- Detail sheet ---- */
  #detail[hidden] { display: none; }
  #detail { position: fixed; inset: 0; z-index: 10; padding: 20px 16px;
            background: rgba(17,17,24,.86); backdrop-filter: blur(8px); -webkit-backdrop-filter: blur(8px);
            display: flex; align-items: flex-start; justify-content: center; overflow: auto;
            animation: fade .15s ease; }
  @keyframes fade { from { opacity: 0; } to { opacity: 1; } }
  @keyframes pop { from { transform: translateY(8px) scale(.98); opacity: 0; } to { transform: none; opacity: 1; } }
  .sheet { width: 100%; max-width: 600px; background: var(--surface); color: var(--fg);
           border: 1px solid var(--border); border-radius: var(--r-sheet); overflow: hidden;
           box-shadow: var(--shadow-sheet); animation: pop .18s ease; }
  .sheet .hero { position: relative; width: 100%; background: var(--surface-2);
                 display: flex; align-items: center; justify-content: center; }
  .sheet .hero img { width: 100%; max-height: 300px; object-fit: contain; display: block; }
  .sheet .close { position: absolute; top: 10px; right: 10px; width: 32px; height: 32px;
                  border: none; border-radius: 50%; cursor: pointer; font-size: 18px; line-height: 32px;
                  color: #fff; background: rgba(0,0,0,.5); backdrop-filter: blur(6px);
                  -webkit-backdrop-filter: blur(6px); text-align: center; transition: background .15s ease; }
  .sheet .close:hover { background: rgba(0,0,0,.72); }
  .sheet .body { padding: 18px 20px 20px; }
  .credit { margin: 0 0 16px; font-size: 14.5px; font-weight: 600; line-height: 1.4; }
  .credit a { color: var(--primary); text-decoration: none; }
  .credit a:hover { text-decoration: underline; }

  .meta { display: grid; grid-template-columns: minmax(96px, auto) 1fr; gap: 9px 16px;
          font-size: 13px; line-height: 1.5; margin: 0 0 16px; }
  .meta dt { color: var(--muted); font-weight: 500; }
  .meta dd { margin: 0; }
  .meta dd.full, .meta dt.full { grid-column: 1 / -1; }
  .meta dt.full { margin-top: 4px; font-size: 11px; text-transform: uppercase; letter-spacing: .5px; }
  .meta dd.full { color: var(--fg); line-height: 1.55; }
  .swatch { display: inline-block; width: 12px; height: 12px; border-radius: 4px;
            border: 1px solid var(--border); vertical-align: -2px; margin-right: 6px; }
  .chip { display: inline-block; padding: 2px 10px; border-radius: 999px;
          background: var(--chip); font-size: 12px; font-weight: 500; }

  .actions { display: flex; flex-wrap: wrap; gap: 9px; justify-content: center;
             padding-top: 4px; border-top: 1px solid var(--border); margin-top: 4px; padding-top: 16px; }
  .btn { appearance: none; border: 1px solid var(--border); cursor: pointer; white-space: nowrap;
         padding: 9px 16px; border-radius: var(--r-btn); font-family: var(--font);
         font-size: 13px; font-weight: 600; color: var(--fg); background: var(--surface-2);
         text-decoration: none; transition: transform .12s ease, background .15s ease, border-color .15s ease; }
  .btn:hover { transform: translateY(-1px); border-color: var(--primary); }
  .btn.primary { background: var(--primary); border-color: var(--primary); color: var(--on-primary);
                 box-shadow: 0 6px 16px rgba(124,58,237,.32); }
  .btn.primary:hover { background: var(--primary-hover); border-color: var(--primary-hover); }
"""

# Widget logic. Reads the SDK from globalThis (inlined) or falls back to a CDN import.
_WIDGET_JS = """
const statusEl = document.getElementById("status");
const gridEl = document.getElementById("grid");
const footEl = document.getElementById("gridfoot");
const detailEl = document.getElementById("detail");

function say(msg, isErr) {
  statusEl.textContent = msg;
  statusEl.className = isErr ? "err" : "";
  statusEl.hidden = false;
  gridEl.hidden = true;
  footEl.hidden = true;
}

let App, applyDocumentTheme;
const sdk = globalThis.__PEXAFY_EXTAPPS__;
if (sdk && sdk.App) {
  App = sdk.App; applyDocumentTheme = sdk.applyDocumentTheme;
} else {
  try {
    const mod = await import("__EXT_APPS_CDN__");
    App = mod.App; applyDocumentTheme = mod.applyDocumentTheme;
  } catch (e) {
    say("Viewer SDK unavailable (CSP/network). " + (e && e.message ? e.message : e), true);
    throw e;
  }
}

// autoResize: report height to the host via ResizeObserver (belt-and-braces; the
// host also auto-sizes on connect, but async-loaded thumbnails grow the grid).
const app = new App({ name: "Pexafy", version: "1.0.0" }, undefined, { autoResize: true });

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"]/g,
    c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

const PEXAFY_HOME = "https://pexafy.com";
const PEXAFY_PHOTO_BASE = "https://pexafy.com/photos/";
// Footer call-to-action — its label+link come from the search context (server `cta`).
let footUrl = PEXAFY_HOME;

// The detail sheet is far taller than a small result grid, so in a short iframe it
// gets clipped. If the host supports it, ask to present the app fullscreen while the
// sheet is open (and revert to inline on close); otherwise we just overlay in-frame.
function hostHasFullscreen() {
  try {
    const c = app.getHostContext && app.getHostContext();
    const modes = c && c.availableDisplayModes;
    return Array.isArray(modes) && modes.indexOf("fullscreen") !== -1;
  } catch (e) { return false; }
}
function setDisplayMode(mode) {
  try { if (app.requestDisplayMode) app.requestDisplayMode({ mode: mode }); } catch (e) {}
}

function fields(photo) {
  const u = photo.urls || {};
  // Hero + thumb both load from Pexafy's signed CDN (the only CSP-allowed origin);
  // the provider's external urls.* would be blocked by the sandbox, so never display
  // them — only hand them to the host via openLink (opens in a real browser tab).
  const thumb = photo.preview_url || u.small || u.thumb || u.regular || "";
  const original = u.full || u.large || u.regular || u.small || "";
  let credit = "";
  const a = photo.attribution;
  if (a && typeof a === "object") credit = a.plain || a.text || "";
  else if (typeof a === "string") credit = a;
  // The plain credit ends with "(<license url>)"; pull it out so we can render a
  // clean "Photo by X on Source" with the URL as a tidy link instead of raw text.
  let licenseUrl = "";
  const mUrl = credit.match(/\\((https?:\\/\\/[^)\\s]+)\\)/);
  if (mUrl) { licenseUrl = mUrl[1]; credit = credit.replace(mUrl[0], "").trim(); }
  const author = photo.photographer_full_name || photo.photographer_username || "";
  const w = photo.width, h = photo.height;
  const pid = photo.photo_id || "";
  return {
    thumb, original, credit, licenseUrl, author,
    rank: photo.rank || 0,
    authorUrl: photo.photographer_url || "",
    source: photo.source || "",
    sourcePage: photo.source_image_url || "",
    license: photo.license_type || "",
    resolution: (w && h) ? (w + " × " + h + " px") : "",
    colorName: photo.color_name || "",
    colorHex: photo.color_hex || "",
    orientation: photo.orientation || "",
    date: photo.uploaded_on || "",
    description: photo.alt_description || "",          // Pexafy: "Description"
    detailed: photo.description || "",                 // Pexafy: "Detailed Description"
    pexafyUrl: pid ? (PEXAFY_PHOTO_BASE + encodeURIComponent(pid)) : "",
  };
}

function cap(s) { s = String(s || ""); return s ? s.charAt(0).toUpperCase() + s.slice(1) : s; }
function row(label, valueHtml) {
  return valueHtml ? "<dt>" + esc(label) + "</dt><dd>" + valueHtml + "</dd>" : "";
}
function fullRow(label, valueHtml) {
  return valueHtml ? '<dt class="full">' + esc(label) + '</dt><dd class="full">' + valueHtml + "</dd>" : "";
}

function creditHtml(f) {
  if (!f.author && !f.source) return esc(f.credit || "");
  const src = f.source
    ? (f.licenseUrl ? '<a data-link="' + esc(f.licenseUrl) + '">' + esc(f.source) + "</a>" : esc(f.source))
    : "";
  return "Photo by " + esc(f.author || "Unknown") + (src ? " on " + src : "");
}

function openDetail(f) {
  const colorDd = (f.colorName || f.colorHex)
    ? (f.colorHex ? '<span class="swatch" style="background:' + esc(f.colorHex) + '"></span>' : "")
      + esc(cap(f.colorName) || f.colorHex)
    : "";
  const authorDd = f.author
    ? (f.authorUrl ? '<a data-link="' + esc(f.authorUrl) + '">' + esc(f.author) + "</a>" : esc(f.author))
    : "";
  const meta =
    row("Photographer", authorDd) +
    row("Source", f.source ? '<span class="chip">' + esc(f.source) + "</span>" : "") +
    row("Resolution", esc(f.resolution)) +
    row("License", f.license ? '<span class="chip">' + esc(f.license) + "</span>" : "") +
    row("Dominant Color", colorDd) +
    row("Orientation", esc(cap(f.orientation))) +
    row("Published", esc(f.date)) +
    fullRow("Description", esc(cap(f.description))) +
    fullRow("Detailed Description", esc(cap(f.detailed)));
  const seeSource = f.source ? "See on " + esc(f.source) : "See source";
  detailEl.innerHTML =
    '<div class="sheet">' +
      '<div class="hero">' +
        '<button class="close" type="button" aria-label="Close">&times;</button>' +
        '<img src="' + esc(f.thumb) + '" alt="' + esc(f.description || f.author || "photo") + '">' +
      "</div>" +
      '<div class="body">' +
        '<p class="credit">' + creditHtml(f) + "</p>" +
        '<dl class="meta">' + meta + "</dl>" +
        '<div class="actions">' +
          (f.original ? '<a class="btn" data-link="' + esc(f.original) + '">Open original image</a>' : "") +
          (f.sourcePage ? '<a class="btn" data-link="' + esc(f.sourcePage) + '">' + seeSource + "</a>" : "") +
          (f.pexafyUrl ? '<a class="btn primary" data-link="' + esc(f.pexafyUrl) + '">See on Pexafy</a>' : "") +
        "</div>" +
      "</div>" +
    "</div>";
  // Wire close + every external link through the host (sandbox-safe).
  detailEl.querySelector(".close").addEventListener("click", closeDetail);
  detailEl.addEventListener("click", (e) => { if (e.target === detailEl) closeDetail(); });
  for (const el of detailEl.querySelectorAll("[data-link]")) {
    el.style.cursor = "pointer";
    el.addEventListener("click", (e) => {
      e.preventDefault();
      try { app.openLink({ url: el.getAttribute("data-link") }); } catch (_) {}
    });
  }
  // Hide the grid behind the sheet so no thumbnails peek around it (cleaner than a blur).
  gridEl.style.visibility = "hidden";
  footEl.style.visibility = "hidden";
  detailEl.hidden = false;
  if (hostHasFullscreen()) {
    setDisplayMode("fullscreen");
  } else {
    // No fullscreen: grow the document so autoResize expands the (possibly short)
    // iframe to fit the sheet — otherwise a tall sheet is clipped in a small grid.
    try {
      const sheet = detailEl.querySelector(".sheet");
      document.body.style.minHeight = ((sheet ? sheet.offsetHeight : 320) + 32) + "px";
    } catch (e) {}
  }
}

function closeDetail() {
  detailEl.hidden = true; detailEl.innerHTML = "";
  document.body.style.minHeight = "";
  gridEl.style.visibility = "";
  footEl.style.visibility = "";
  if (hostHasFullscreen()) setDisplayMode("inline");
}

const GRID_MAX = 20;
function render(sc) {
  let data = (sc && (sc.data || sc.results || sc.photos)) || [];
  if (!Array.isArray(data) || data.length === 0) { say("No images to display."); return; }
  data = data.slice(0, GRID_MAX);  // visual cap — keep the grid coherent
  gridEl.innerHTML = "";
  let shown = 0;
  data.forEach((photo, i) => {
    const f = fields(photo || {});
    if (!f.thumb) return;
    const rank = f.rank || (i + 1);   // fall back to position if the server omitted rank
    const card = document.createElement("div");
    card.className = "card";
    // Thumbnail chrome: rank (top-left), source (top-right), photographer credit (bottom).
    card.innerHTML =
      '<img loading="lazy" src="' + esc(f.thumb) + '" alt="' + esc(f.description || "photo") + '">' +
      '<span class="rank">#' + esc(rank) + "</span>" +
      (f.source ? '<span class="src">' + esc(f.source) + "</span>" : "") +
      (f.author ? '<div class="credit-bar">By ' + esc(f.author) + "</div>" : "");
    card.addEventListener("click", () => { try { openDetail(f); } catch (e) {} });
    gridEl.appendChild(card);
    shown++;
  });
  if (shown === 0) { say("No previewable images in these results."); return; }
  // Contextual footer link: "More at Pexafy" (text/similar) or "Try it at Pexafy" (image).
  const cta = sc && sc.cta;
  footUrl = (cta && cta.url) || PEXAFY_HOME;
  footEl.innerHTML =
    '<span class="brand"><span class="dot"></span>' + esc((cta && cta.label) || "By Pexafy") + "</span>";
  statusEl.hidden = true;
  gridEl.hidden = false;
  footEl.hidden = false;
}

document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !detailEl.hidden) closeDetail(); });
footEl.addEventListener("click", () => { try { app.openLink({ url: footUrl }); } catch (e) {} });

app.ontoolresult = (params) => {
  try { render(params && params.structuredContent); }
  catch (e) { say("Could not render the results. " + (e && e.message ? e.message : ""), true); }
};
app.onhostcontextchanged = (ctx) => { try { applyDocumentTheme(ctx && ctx.theme); } catch (e) {} };
app.onerror = (e) => { try { console.error(e); } catch (_) {} };

try {
  await app.connect();
  try { const c = app.getHostContext(); if (c) applyDocumentTheme(c.theme); } catch (e) {}
  if (gridEl.hidden && statusEl.textContent.indexOf("loading") !== -1) {
    say("Connected — waiting for results…");
  }
} catch (e) {
  say("Could not connect to the host. " + (e && e.message ? e.message : e), true);
}
""".replace("__EXT_APPS_CDN__", _EXT_APPS_CDN)


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
        f"<style>{_FONT_CSS}</style>\n"
        f"<style>{_STYLE}</style>\n</head>\n<body>\n"
        '<div id="status">Pexafy viewer — loading…</div>\n'
        '<div class="grid" id="grid" hidden></div>\n'
        '<div class="gridfoot" id="gridfoot" hidden>'
        '<span class="brand"><span class="dot"></span>By Pexafy</span></div>\n'
        '<div id="detail" hidden></div>\n'
        f"{sdk_script}"
        f'<script type="module">{_WIDGET_JS}</script>\n'
        "</body>\n</html>\n"
    )


GRID_HTML = _build_html()
