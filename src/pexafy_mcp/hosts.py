"""Which host is on the other end — for the two answers the specifications leave to it.

`_meta.ui.domain` on the grid resource. The MCP Apps specification leaves the field's
format to each host ("Host-dependent: The format and validation rules for this field are
determined by each host"), and the two hosts that render the grid want different things
in it:

  * Claude accepts nothing but `{hash}.claudemcpcontent.com` — the SHA-256 of this
    server's endpoint — and renders no widget at all otherwise;
  * OpenAI reads it as the "Dedicated origin for hosted components (required when
    submitting a plugin with UI; must be unique per plugin)", `openai/widgetDomain`
    being only its "OpenAI-specific compatibility alias" — so a Claude hash there is the
    domain OpenAI's review would see first.

Claude is the host that can be recognised, so it is the one recognised: a client whose
name says Claude or Anthropic, or that calls from the network Anthropic's servers call
from, gets the hash, and every other one gets the website — an OpenAI host, but also a
client this module cannot place, since OpenAI's review scans the server with a client
whose name is not documented.

The photo file's second copy (server.get_photo_file). The bytes go out as an image and,
for ChatGPT, a second time as an embedded resource, the form it may attach rather than
look at. Claude caps a tool result — about 150,000 characters on claude.ai, 25,000
tokens in Claude Code — and a second base64 copy of a 1280-pixel photograph is exactly
what hits the cap.

The host names itself in `clientInfo`: once, in the session's `initialize`, or — for a
client of protocol revision 2026-07-28, which opens no session — in the `_meta` of every
request (observe.META_CLIENT_INFO). Measured: ChatGPT sends `openai-mcp` (its User-Agent
reads `openai-mcp/1.0.0` too); Claude sends `Anthropic/ClaudeAI`, `claude-ai`,
`Anthropic` or `claude-code`, depending on the surface. On a tool call, ChatGPT also
puts `openai/…` keys in the request's `_meta` (`openai/subject` on every call measured),
which no other host does. Either is taken as the mark of an OpenAI host.

Claude's names "vary across surfaces, request paths, and releases" (Anthropic's own
testing guide), and a name can be missing altogether: a session without `clientInfo`, a
stateless transport, a surface that has yet to ship. A Claude host served the website
there would refuse the grid ("Invalid ui.domain format"). So the network counts too:
every request from Anthropic's servers — Claude.ai's connections among them — leaves
`anonymous.ANTHROPIC_OUTBOUND`, and OpenAI's never does. Claude Code, which calls from
the user's own machine, names itself.

Codex is the host its name does not tell apart. Through OpenAI's apps platform it calls
as ChatGPT does — its `clientInfo` most probably the same `openai-mcp` — and only its
User-Agent differs, by a suffix. Codex CLI, connected directly, names itself
`codex-mcp-client`. MCP requests in production's access log over 14 days, by
User-Agent (measured 2026-10-02):

    openai-mcp/1.0.0            10,835   ChatGPT
    openai-mcp/1.0.0 (Codex)     4,439   Codex, through the apps platform
    codex-mcp-client/0.159.x         —   Codex CLI, direct

A `(Codex)` in the request's User-Agent, or a `clientInfo` naming Codex, is Codex
(is_codex). It stays an OpenAI host: it comes through the same platform, so it keeps what
compat.py keeps for that platform — the 0.4.12 grid's window. But it is not known to show the grid, and taken for ChatGPT it was handed the
photographs without a single link (previews.grid_summary): its user had no way left to
see or use them. So it reads the whole answer, links included, like any client without
the grid and as in 0.4.12 — unless it declares MCP Apps itself (renders_grid). Its
assistant is Codex (assistant_name).

Anthropic's guidance on `clientInfo` holds for every host: "Use clientInfo for telemetry
and coarse feature detection only. It's also unauthenticated: any client can claim any
name, so it must never feed an authorization decision." The same goes for an address a
proxy header carries, and for a User-Agent. Nothing here authorizes anything: a client
that claims to be ChatGPT gets ChatGPT's widget origin and a second copy of a file it
asked for, one that claims to be Claude gets Claude's, one that claims to be Codex gets
the image links every client without the grid gets. A client this module cannot place —
no name that matches, an address outside Anthropic's — gets the website as `ui.domain`
(see above) and one copy of a file, as before hosts were told apart.
"""
from __future__ import annotations

import copy
import ipaddress
import logging
from typing import Any

from fastmcp.server.middleware import Middleware, MiddlewareContext

from . import anonymous, observe

logger = logging.getLogger("pexafy.mcp.hosts")

# Lowercased substrings of `clientInfo.name` that mark an OpenAI host ("openai-mcp").
OPENAI_CLIENT_MARKERS = ("openai", "chatgpt")

# …and a Claude host ("Anthropic/ClaudeAI", "claude-ai", "Anthropic", "claude-code").
CLAUDE_CLIENT_MARKERS = ("claude", "anthropic")

# The prefix of the `_meta` keys only an OpenAI host sends with a request.
OPENAI_META_PREFIX = "openai/"

# What tells Codex from ChatGPT through OpenAI's apps platform, where both call as
# `openai-mcp`: the suffix of its User-Agent ("openai-mcp/1.0.0 (Codex)"), lowercased.
CODEX_USER_AGENT_MARKER = "(codex)"

# …and a client that names itself Codex: Codex CLI, connected directly ("codex-mcp-client").
CODEX_CLIENT_MARKERS = ("codex",)

# Where every request from Anthropic's servers comes from.
ANTHROPIC_NETWORK = ipaddress.ip_network(anonymous.ANTHROPIC_OUTBOUND)


def _context(context: Any = None):
    """The FastMCP context: the one a middleware context holds, else the current
    request's, else None."""
    held = getattr(context, "fastmcp_context", None)
    if held is not None:
        return held
    try:
        from fastmcp.server.dependencies import get_context

        return get_context()
    except Exception:  # noqa: BLE001 — outside a request (a unit call): no host at all
        return None


def _request_meta(context: Any = None) -> dict:
    """The `_meta` the client sent with the current request — its own keys — or {}."""
    ctx = _context(context)
    try:
        meta = getattr(ctx.request_context, "meta", None)
    except Exception:  # noqa: BLE001 — no request in scope
        return {}
    extra = getattr(meta, "model_extra", None)
    return extra if isinstance(extra, dict) else {}


def client_name(context: Any = None) -> str:
    """The name the client gives itself: `clientInfo.name` from the session's
    `initialize`, else from the request's `_meta`, else ""."""
    ctx = _context(context)
    if ctx is None:
        return ""
    try:
        params = ctx.session.client_params
    except Exception:  # noqa: BLE001 — no session established (stateless, unit call)
        params = None
    info = getattr(params, "clientInfo", None)
    name = str(getattr(info, "name", "") or "")
    if name:
        return name
    held = _request_meta(context).get(observe.META_CLIENT_INFO)
    return str(held.get("name") or "") if isinstance(held, dict) else ""


def _request_meta_keys(context: Any = None) -> list[str]:
    """The names of the `_meta` keys the client sent with the current request."""
    return [str(k) for k in _request_meta(context)]


def calls_from_anthropic() -> bool:
    """True when the current request comes from the network Anthropic's servers call
    from (ANTHROPIC_NETWORK), as the proxies in front of this server report the
    caller's address. False outside an HTTP request."""
    ip = anonymous.client_ip(anonymous._incoming_headers())
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if address.version == 6 and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return address in ANTHROPIC_NETWORK


def is_openai(context: Any = None) -> bool:
    """True when the caller is an OpenAI host (ChatGPT). False when it is anything
    else, or cannot be told — the answer that keeps the server's earlier behaviour."""
    name = client_name(context).lower()
    if any(marker in name for marker in OPENAI_CLIENT_MARKERS):
        return True
    return any(key.startswith(OPENAI_META_PREFIX) for key in _request_meta_keys(context))


def is_claude(context: Any = None) -> bool:
    """True when the caller is a Claude host: it names itself Claude or Anthropic — in
    its session or in the request's `_meta` — or it calls from Anthropic's network.
    False for any other host, and for one that cannot be told."""
    name = client_name(context).lower()
    if any(marker in name for marker in CLAUDE_CLIENT_MARKERS):
        return True
    return calls_from_anthropic()


def _user_agent() -> str:
    """The current HTTP request's User-Agent, lowercased, or "" outside one."""
    return str(anonymous._incoming_headers().get("user-agent") or "").lower()


def is_codex(context: Any = None) -> bool:
    """True when the caller is Codex: the request's User-Agent says `(Codex)`, in any
    case — the one mark of Codex through OpenAI's apps platform, which names it as it
    names ChatGPT — or the client names itself Codex (`codex-mcp-client`). Codex through
    the platform is an OpenAI host all the same (is_openai)."""
    if CODEX_USER_AGENT_MARKER in _user_agent():
        return True
    name = client_name(context).lower()
    return any(marker in name for marker in CODEX_CLIENT_MARKERS)


# ── Who shows the grid ──────────────────────────────────────────────────────
# The clients known to show MCP Apps — the Pexafy grid — to the person, by a lowercase
# substring of `clientInfo.name`, for those that do not say so in their capabilities.
# Claude Code is a terminal: it names itself "claude-code" and shows no grid, so the
# Claude markers above are no use here. Codex calls as ChatGPT does, `openai-mcp`, and
# is not known to show it: it is told apart (is_codex) before these are read.
GRID_CLIENT_MARKERS = ("openai", "chatgpt", "claude-ai", "claudeai", "visual studio code",
                       "vscode", "cursor", "goose", "mcpjam")
NO_GRID_CLIENT_MARKERS = ("claude-code",)


def declares_apps(context: Any = None) -> bool:
    """Whether the client said at `initialize` that it shows MCP Apps: the
    `io.modelcontextprotocol/ui` extension, under `extensions` (the SDK keeps it as an
    extra field) or `experimental`, for the grid's MIME type or for any."""
    from fastmcp.apps import UI_EXTENSION_ID, UI_MIME_TYPE

    ctx = _context(context)
    try:
        caps = ctx.session.client_params.capabilities
    except Exception:  # noqa: BLE001 — no session (stateless, unit call): not said
        return False
    found = ((getattr(caps, "model_extra", None) or {}).get("extensions") or {}).get(UI_EXTENSION_ID)
    if found is None:
        found = (getattr(caps, "experimental", None) or {}).get(UI_EXTENSION_ID)
    if found is None:
        return False
    mimes = found.get("mimeTypes") if isinstance(found, dict) else None
    return not mimes or UI_MIME_TYPE in mimes


def renders_grid(context: Any = None) -> bool:
    """Whether the person sees this answer as the Pexafy grid: the client says it shows
    MCP Apps, or it is one known to — ChatGPT, claude.ai and Claude Desktop (which call
    from Anthropic's network), VS Code, Cursor, Goose. Claude Code, Codex and every
    other terminal or unknown client: no. Codex, an OpenAI host that is not known to
    show the grid, is taken at its word when it declares MCP Apps itself."""
    name = client_name(context).lower()
    if any(marker in name for marker in NO_GRID_CLIENT_MARKERS):
        return False
    if declares_apps(context):
        return True
    if is_codex(context):
        return False
    if any(marker in name for marker in GRID_CLIENT_MARKERS):
        return True
    return is_openai(context) or calls_from_anthropic()


# The assistant a person talks to in each host that shows the grid: whom the grid names
# when it says where the photos they like go. In VS Code that is GitHub Copilot.
ASSISTANT_NAMES = (
    (OPENAI_CLIENT_MARKERS, "ChatGPT"),
    (CLAUDE_CLIENT_MARKERS, "Claude"),
    # Before VS Code: Cursor names itself "cursor-vscode".
    (("cursor",), "Cursor"),
    (("visual studio code", "vscode"), "GitHub Copilot"),
    (("goose",), "Goose"),
)


def assistant_name(context: Any = None) -> str:
    """The assistant's name for the grid to say, or "" for a host not known here (the
    grid then says "the assistant")."""
    # Before ChatGPT: through OpenAI's apps platform, Codex is an OpenAI host too, and
    # its grid would tell the person their photos go to ChatGPT.
    if is_codex(context):
        return "Codex"
    if is_openai(context):
        return "ChatGPT"
    name = client_name(context).lower()
    for markers, label in ASSISTANT_NAMES:
        if any(marker in name for marker in markers):
            return label
    return "Claude" if calls_from_anthropic() else ""


def _with_domain(meta: dict | None, domain: str) -> dict:
    """A copy of a resource's `_meta` with `ui.domain` set — never the registered dict,
    which every other caller reads."""
    out = copy.deepcopy(meta) if isinstance(meta, dict) else {}
    ui = out.get("ui")
    ui = dict(ui) if isinstance(ui, dict) else {}
    ui["domain"] = domain
    out["ui"] = ui
    return out


class WidgetDomainPerHost(Middleware):
    """Serve the grid resource's `_meta.ui.domain` in the format the calling host checks.

    What is registered is Claude's value (server._spec_ui_domain), and a Claude host gets
    it untouched. Every other host gets `openai_domain` instead — the origin
    `openai/widgetDomain` already carries — in `resources/list` and in `resources/read`,
    where OpenAI documents the field: an OpenAI host, and one that cannot be placed,
    OpenAI's review scan included whatever its client calls itself.
    """

    def __init__(self, uri: str, openai_domain: str):
        self.uri = uri
        self.openai_domain = openai_domain

    def _applies(self, context: MiddlewareContext) -> bool:
        return bool(self.openai_domain) and not is_claude(context)

    async def on_list_resources(self, context: MiddlewareContext, call_next):
        resources = await call_next(context)
        if not self._applies(context):
            return resources
        out = []
        for resource in resources:
            if str(getattr(resource, "uri", "")) == self.uri:
                resource = resource.model_copy(
                    update={"meta": _with_domain(resource.meta, self.openai_domain)}
                )
            out.append(resource)
        return out

    async def on_read_resource(self, context: MiddlewareContext, call_next):
        result = await call_next(context)
        uri = str(getattr(getattr(context, "message", None), "uri", ""))
        if uri != self.uri or not self._applies(context):
            return result
        for content in getattr(result, "contents", None) or []:
            content.meta = _with_domain(content.meta, self.openai_domain)
        return result



# Hosts known to show neither the app's name nor its logo above the frame, by the name
# they give (measured in the preprod logs: "Visual Studio Code", "cursor-vscode"). A list,
# not "anything that is neither ChatGPT nor Claude": OpenAI's review scan calls itself
# something undocumented (see WidgetDomainPerHost), and the page it reads must stay the
# one ChatGPT gets.
NO_IDENTITY_CLIENT_MARKERS = ("visual studio code", "vscode", "cursor", "goose", "mcpjam")


def draws_no_app_identity(context: Any = None) -> bool:
    """True for a host on NO_IDENTITY_CLIENT_MARKERS that is neither ChatGPT nor Claude."""
    name = client_name(context).lower()
    if not any(marker in name for marker in NO_IDENTITY_CLIENT_MARKERS):
        return False
    return not is_openai(context) and not is_claude(context)


class LogoWhereTheHostDrawsNone(Middleware):
    """Serve the grid page with the site's logo rule to the hosts that draw no name
    above the frame (draws_no_app_identity); every other host gets the page as built.

    ChatGPT shows the app's logo and name before the frame and OpenAI's guidelines forbid
    a second one, and custom gradients: the rule is never in the page it reads. `decorate`
    is widget.with_logo, handed in by the server (widget knows nothing of hosts). The page
    is rewritten on a copy, so the next host never inherits the last one's.
    """

    def __init__(self, uri: str, decorate):
        self.uri = uri
        self.decorate = decorate

    async def on_read_resource(self, context: MiddlewareContext, call_next):
        result = await call_next(context)
        uri = str(getattr(getattr(context, "message", None), "uri", ""))
        if uri != self.uri or not draws_no_app_identity(context):
            return result
        contents = getattr(result, "contents", None)
        if not contents:
            return result
        fresh = []
        for content in contents:
            # FastMCP's ResourceContent holds the page in `content`; the protocol's
            # TextResourceContents, in `text`.
            for field in ("content", "text"):
                page = getattr(content, field, None)
                if isinstance(page, str):
                    content = copy.copy(content)
                    setattr(content, field, self.decorate(page))
                    break
            fresh.append(content)
        result.contents = fresh
        return result
