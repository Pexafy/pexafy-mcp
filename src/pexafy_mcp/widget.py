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
server-signed `preview_url`, see previews.inject_preview_urls), paints a
responsive grid of thumbnails, and opens the full image via the host (`app.openLink`)
on click. Thumbnails load from Pexafy's own signed thumb CDN (one CSP origin we
control); the HMAC secret never reaches the iframe.
"""
from __future__ import annotations

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

# Resource CSP needs the thumb CDN for images; the SDK is inlined (no CDN) unless the
# vendored bundle is missing, in which case esm.sh is needed for the fallback import.
RESOURCE_EXTRA_DOMAINS: list[str] = [] if SDK_INLINED else [EXT_APPS_ORIGIN]


_STYLE = """
  :root { color-scheme: light dark; }
  * { box-sizing: border-box; }
  html, body { margin: 0; }
  body { min-height: 64px; background: transparent;
         font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
         color: #1d1d1f; }
  @media (prefers-color-scheme: dark) { body { color: #ececec; } }
  #status { padding: 14px 16px; font-size: 13px; line-height: 1.4; }
  #status.err { color: #b42318; }
  .grid { display: grid; gap: 10px; padding: 12px;
          grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); }
  .card { position: relative; aspect-ratio: 1 / 1; border-radius: 10px; overflow: hidden;
          cursor: pointer; background: rgba(127,127,127,.12);
          border: 1px solid rgba(127,127,127,.18); transition: transform .12s ease; }
  .card:hover { transform: translateY(-2px); }
  .card img { width: 100%; height: 100%; object-fit: cover; display: block; }
  .cap { position: absolute; inset: auto 0 0 0; padding: 6px 8px; font-size: 11px; line-height: 1.3;
         color: #fff; background: linear-gradient(transparent, rgba(0,0,0,.74));
         opacity: 0; transition: opacity .12s ease; pointer-events: none;
         white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .card:hover .cap { opacity: 1; }
"""

# Widget logic. Reads the SDK from globalThis (inlined) or falls back to a CDN import.
_WIDGET_JS = """
const statusEl = document.getElementById("status");
const gridEl = document.getElementById("grid");

function say(msg, isErr) {
  statusEl.textContent = msg;
  statusEl.className = isErr ? "err" : "";
  statusEl.hidden = false;
  gridEl.hidden = true;
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

function fields(photo) {
  const u = photo.urls || {};
  const thumb = photo.preview_url || u.small || u.thumb || u.regular || "";
  const open = u.regular || u.large || u.full || u.small || thumb;
  let credit = "";
  const a = photo.attribution;
  if (a && typeof a === "object") credit = a.plain || a.text || "";
  else if (typeof a === "string") credit = a;
  return { thumb, open, credit };
}

const GRID_MAX = 20;
function render(sc) {
  let data = (sc && (sc.data || sc.results || sc.photos)) || [];
  if (!Array.isArray(data) || data.length === 0) { say("No images to display."); return; }
  data = data.slice(0, GRID_MAX);  // visual cap — keep the grid coherent
  gridEl.innerHTML = "";
  let shown = 0;
  for (const photo of data) {
    const { thumb, open, credit } = fields(photo || {});
    if (!thumb) continue;
    const card = document.createElement("div");
    card.className = "card";
    card.innerHTML =
      '<img loading="lazy" src="' + esc(thumb) + '" alt="' + esc(credit || "photo") + '">' +
      (credit ? '<div class="cap">' + esc(credit) + "</div>" : "");
    if (open) card.addEventListener("click", () => { try { app.openLink({ url: open }); } catch (e) {} });
    gridEl.appendChild(card);
    shown++;
  }
  if (shown === 0) { say("No previewable images in these results."); return; }
  statusEl.hidden = true;
  gridEl.hidden = false;
}

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
        f"<style>{_STYLE}</style>\n</head>\n<body>\n"
        '<div id="status">Pexafy viewer — loading…</div>\n'
        '<div class="grid" id="grid" hidden></div>\n'
        f"{sdk_script}"
        f'<script type="module">{_WIDGET_JS}</script>\n'
        "</body>\n</html>\n"
    )


GRID_HTML = _build_html()
