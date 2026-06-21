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
  :root { color-scheme: light dark; --pxf-fg: #1d1d1f; --pxf-muted: #6b6b70;
          --pxf-card: rgba(127,127,127,.12); --pxf-border: rgba(127,127,127,.18);
          --pxf-panel: #ffffff; --pxf-chip: rgba(127,127,127,.10); }
  @media (prefers-color-scheme: dark) {
    :root { --pxf-fg: #ececec; --pxf-muted: #a1a1a6; --pxf-panel: #1c1c1e;
            --pxf-chip: rgba(255,255,255,.08); } }
  * { box-sizing: border-box; }
  html, body { margin: 0; }
  body { min-height: 64px; background: transparent;
         font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
         color: var(--pxf-fg); }
  #status { padding: 14px 16px; font-size: 13px; line-height: 1.4; }
  #status.err { color: #b42318; }
  .grid { display: grid; gap: 10px; padding: 12px;
          grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); }
  .card { position: relative; aspect-ratio: 1 / 1; border-radius: 10px; overflow: hidden;
          cursor: pointer; background: var(--pxf-card);
          border: 1px solid var(--pxf-border); transition: transform .12s ease; }
  .card:hover { transform: translateY(-2px); }
  .card img { width: 100%; height: 100%; object-fit: cover; display: block; }
  .cap { position: absolute; inset: auto 0 0 0; padding: 6px 8px; font-size: 11px; line-height: 1.3;
         color: #fff; background: linear-gradient(transparent, rgba(0,0,0,.74));
         opacity: 0; transition: opacity .12s ease; pointer-events: none;
         white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .card:hover .cap { opacity: 1; }

  /* Detail panel — opened on card click, overlays the whole widget. */
  #detail[hidden] { display: none; }
  #detail { position: fixed; inset: 0; z-index: 10; padding: 14px;
            background: rgba(0,0,0,.34); backdrop-filter: blur(2px);
            display: flex; align-items: flex-start; justify-content: center; overflow: auto; }
  .sheet { width: 100%; max-width: 560px; background: var(--pxf-panel); color: var(--pxf-fg);
           border: 1px solid var(--pxf-border); border-radius: 14px; overflow: hidden;
           box-shadow: 0 12px 40px rgba(0,0,0,.35); }
  .sheet .hero { position: relative; width: 100%; background: var(--pxf-card); }
  .sheet .hero img { width: 100%; max-height: 320px; object-fit: contain; display: block; }
  .sheet .close { position: absolute; top: 8px; right: 8px; width: 30px; height: 30px;
                  border: none; border-radius: 50%; cursor: pointer; font-size: 17px; line-height: 30px;
                  color: #fff; background: rgba(0,0,0,.55); text-align: center; }
  .sheet .body { padding: 14px 16px 16px; }
  .sheet h2 { margin: 0 0 2px; font-size: 15px; font-weight: 600; }
  .sheet .by { margin: 0 0 12px; font-size: 12.5px; color: var(--pxf-muted); }
  .sheet .by a { color: inherit; }
  .meta { display: grid; grid-template-columns: auto 1fr; gap: 6px 14px;
          font-size: 12.5px; line-height: 1.45; margin: 0 0 14px; }
  .meta dt { color: var(--pxf-muted); white-space: nowrap; }
  .meta dd { margin: 0; }
  .swatch { display: inline-block; width: 11px; height: 11px; border-radius: 3px;
            border: 1px solid var(--pxf-border); vertical-align: -1px; margin-right: 5px; }
  .chip { display: inline-block; padding: 1px 8px; border-radius: 999px;
          background: var(--pxf-chip); font-size: 11.5px; }
  .desc { font-size: 12.5px; color: var(--pxf-muted); line-height: 1.5; margin: 0 0 14px; }
  .actions { display: flex; flex-wrap: wrap; gap: 8px; }
  .btn { appearance: none; border: 1px solid var(--pxf-border); cursor: pointer;
         padding: 8px 14px; border-radius: 9px; font-size: 12.5px; font-weight: 600;
         color: var(--pxf-fg); background: transparent; text-decoration: none; }
  .btn.primary { background: #2563eb; border-color: #2563eb; color: #fff; }
"""

# Widget logic. Reads the SDK from globalThis (inlined) or falls back to a CDN import.
_WIDGET_JS = """
const statusEl = document.getElementById("status");
const gridEl = document.getElementById("grid");
const detailEl = document.getElementById("detail");

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
  const big = u.regular || u.large || u.full || u.small || thumb;   // best image to open
  const open = u.full || u.large || u.regular || big;
  let credit = "";
  const a = photo.attribution;
  if (a && typeof a === "object") credit = a.plain || a.text || "";
  else if (typeof a === "string") credit = a;
  const author = photo.photographer_full_name || photo.photographer_username || "";
  const w = photo.width, h = photo.height;
  return {
    thumb, big, open, credit, author,
    authorUrl: photo.photographer_url || "",
    source: photo.source || "",
    sourcePage: photo.source_image_url || "",
    license: photo.license_type || "",
    resolution: (w && h) ? (w + " × " + h) : "",
    colorName: photo.color_name || "",
    colorHex: photo.color_hex || "",
    orientation: photo.orientation || "",
    date: photo.uploaded_on || "",
    caption: photo.description || photo.alt_description || "",
  };
}

function row(label, valueHtml) {
  return valueHtml ? "<dt>" + esc(label) + "</dt><dd>" + valueHtml + "</dd>" : "";
}

function openDetail(f) {
  const colorDd = f.colorName
    ? (f.colorHex ? '<span class="swatch" style="background:' + esc(f.colorHex) + '"></span>' : "") + esc(f.colorName)
    : (f.colorHex ? '<span class="swatch" style="background:' + esc(f.colorHex) + '"></span>' + esc(f.colorHex) : "");
  const authorDd = f.author
    ? (f.authorUrl ? '<a data-link="' + esc(f.authorUrl) + '">' + esc(f.author) + "</a>" : esc(f.author))
    : "";
  const meta =
    row("Photographer", authorDd) +
    row("Source", f.source ? '<span class="chip">' + esc(f.source) + "</span>" : "") +
    row("Resolution", esc(f.resolution)) +
    row("License", f.license ? '<span class="chip">' + esc(f.license) + "</span>" : "") +
    row("Color", colorDd) +
    row("Orientation", esc(f.orientation)) +
    row("Published", esc(f.date));
  detailEl.innerHTML =
    '<div class="sheet">' +
      '<div class="hero">' +
        '<button class="close" type="button" aria-label="Close">&times;</button>' +
        '<img src="' + esc(f.big) + '" alt="' + esc(f.credit || "photo") + '">' +
      "</div>" +
      '<div class="body">' +
        (f.author ? "<h2>" + esc(f.author) + "</h2>" : "<h2>Photo</h2>") +
        (f.credit ? '<p class="by">' + esc(f.credit) + "</p>" : "") +
        '<dl class="meta">' + meta + "</dl>" +
        (f.caption ? '<p class="desc">' + esc(f.caption) + "</p>" : "") +
        '<div class="actions">' +
          (f.open ? '<a class="btn primary" data-link="' + esc(f.open) + '">Open original</a>' : "") +
          (f.sourcePage ? '<a class="btn" data-link="' + esc(f.sourcePage) + '">Source page</a>' : "") +
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
  detailEl.hidden = false;
}

function closeDetail() { detailEl.hidden = true; detailEl.innerHTML = ""; }

const GRID_MAX = 20;
function render(sc) {
  let data = (sc && (sc.data || sc.results || sc.photos)) || [];
  if (!Array.isArray(data) || data.length === 0) { say("No images to display."); return; }
  data = data.slice(0, GRID_MAX);  // visual cap — keep the grid coherent
  gridEl.innerHTML = "";
  let shown = 0;
  for (const photo of data) {
    const f = fields(photo || {});
    if (!f.thumb) continue;
    const card = document.createElement("div");
    card.className = "card";
    card.innerHTML =
      '<img loading="lazy" src="' + esc(f.thumb) + '" alt="' + esc(f.credit || "photo") + '">' +
      (f.credit ? '<div class="cap">' + esc(f.credit) + "</div>" : "");
    card.addEventListener("click", () => { try { openDetail(f); } catch (e) {} });
    gridEl.appendChild(card);
    shown++;
  }
  if (shown === 0) { say("No previewable images in these results."); return; }
  statusEl.hidden = true;
  gridEl.hidden = false;
}

document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !detailEl.hidden) closeDetail(); });

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
        '<div id="detail" hidden></div>\n'
        f"{sdk_script}"
        f'<script type="module">{_WIDGET_JS}</script>\n'
        "</body>\n</html>\n"
    )


GRID_HTML = _build_html()
