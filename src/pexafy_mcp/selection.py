"""What the reader picked in the grid, kept where the model can always fetch it.

The problem this exists for
---------------------------
The grid publishes the reader's selection to the model on every heart, through the two
channels the Apps SDK documents — `ui/update-model-context` and the `modelContent` half
of `setWidgetState`. That is the right thing to do and it is not enough: ChatGPT reports
the context as sent and then answers later turns from a stale one, or from none at all
(openai/openai-apps-sdk-examples#221, open, no workaround offered). Readers keep two
photographs, ask what they picked, and are told the selection is not available.

Nothing inside the frame can fix a host that does not read what it was handed. So the
selection also travels the one path that does not depend on the host reading anything:
the widget POSTs it to /selection, and the selection tool (SELECTED_TOOL) — a tool like
any other — reads it back. A tool result is the one thing a model always sees.

How a frame is allowed to write
-------------------------------
Every answer the grid draws carries a short-lived token in its `_meta`, signed with a
server secret (the anonymous principal's, PEXAFY_SELECTION_SECRET, or one of this
process's own). The token names the caller — as a digest, never a subject — and
expires. A frame can therefore write only the selection of the person whose answer it
is rendering, for as long as that answer is fresh, and it needs no credential of its
own: the iframe has none to give, and one handed to it would be a credential published
to every reader.

What is stored is what the grid already shows: photo ids and the public metadata that
came back with them. No token is stored, nothing is logged that identifies a person, and
an entry falls out of the store on its own after SELECTION_TTL.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time

from . import anonymous, tooling
from .env import flag

logger = logging.getLogger("pexafy.mcp.selection")

# How long a selection is worth answering with. Long enough for a conversation to come
# back to it, short enough that a shortlist from this morning is not served as "yours".
SELECTION_TTL = int(os.environ.get("PEXAFY_SELECTION_TTL", "3600"))
# How long the widget may keep writing against one answer's token.
TOKEN_TTL = int(os.environ.get("PEXAFY_SELECTION_TOKEN_TTL", "7200"))
# Ceilings, so a hostile or looping frame cannot grow the process.
MAX_ITEMS = 60
MAX_KEYS = 5000
MAX_BODY = 256 * 1024
MAX_TEXT = 400

# The public URL the widget posts to. Empty when the server does not know its own
# address, in which case no token is issued and the widget simply does not post.
PUBLIC_URL = (os.environ.get("PEXAFY_MCP_PUBLIC_URL", "") or "").rstrip("/")

_LOCK = threading.Lock()
_STORE: dict[str, dict] = {}
# When a grid was last SENT to this caller. Measured here because it is the one thing
# that dates a selection: a selection written before the grid on screen was drawn
# belongs to a grid that is no longer on screen.
_GRIDS: dict[str, float] = {}


# ── Who a selection belongs to ───────────────────────────────────────────────
def key_for_caller() -> str:
    """A stable, opaque name for the current caller — the anonymous principal when
    there is one, otherwise the credential they presented.

    Never the subject itself and never the key itself: both are hashed, because this
    value ends up in a token the browser holds.
    """
    identity = anonymous.current.get()
    if identity is not None:
        return "a:" + anonymous.digest(identity)
    auth = ""
    try:
        auth = anonymous._incoming_headers().get("authorization", "")
    except Exception:  # noqa: BLE001 — no request in scope
        auth = ""
    if auth:
        return "k:" + hashlib.sha256(auth.encode()).hexdigest()[:24]
    return ""


# A secret of this process's own, when nothing else configures one. The store it
# protects is this process's memory, so a key that dies with the process is exactly the
# right lifetime — and it means the channel works out of the box rather than silently
# staying off on a server that never configured anonymous access.
_PROCESS_SECRET = secrets.token_hex(32)


def _secret() -> str:
    return (anonymous.SECRET
            or os.environ.get("PEXAFY_SELECTION_SECRET", "")
            or _PROCESS_SECRET)


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64url(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def issue_token(key: str, *, now: float | None = None) -> str:
    """A capability to write ONE caller's selection, for a while."""
    secret = _secret()
    if not key or not secret:
        return ""
    issued = int(now if now is not None else time.time())
    # `n`: one token per answer, even two in the same second for the same caller — the
    # view of a grid is kept under the token of its answer (view_key).
    body = _b64url(json.dumps({"k": key, "x": issued + TOKEN_TTL, "n": secrets.token_hex(4)},
                              separators=(",", ":"), sort_keys=True).encode())
    mac = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{body}.{mac}"


def read_token(token: str, *, now: float | None = None) -> str:
    """The key a token names, or "" if it is not ours, is malformed, or has expired."""
    secret = _secret()
    # Every token issued here is ASCII. Anything else is forged, and would break the
    # comparison below (TypeError on a non-ASCII str) or the encoding before it (a lone
    # surrogate, which a JSON body can carry).
    if not token or not token.isascii() or not secret or "." not in token:
        return ""
    body, _, mac = token.partition(".")
    want = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(mac.encode(), want.encode()):
        return ""
    try:
        payload = json.loads(_unb64url(body))
    except Exception:  # noqa: BLE001 — a forged body is not an error worth a trace
        return ""
    if not isinstance(payload, dict):
        return ""
    if float(payload.get("x") or 0) < (now if now is not None else time.time()):
        return ""
    key = payload.get("k")
    return key if isinstance(key, str) else ""


# ── The store ────────────────────────────────────────────────────────────────
def _sweep(now: float) -> None:
    dead = [k for k, v in _STORE.items() if now - v["at"] > SELECTION_TTL]
    for k in dead:
        _STORE.pop(k, None)
    if len(_STORE) > MAX_KEYS:                       # oldest first
        for k, _ in sorted(_STORE.items(), key=lambda kv: kv[1]["at"])[:len(_STORE) - MAX_KEYS]:
            _STORE.pop(k, None)


def _clean_items(items) -> list[dict]:
    """Only the fields the grid already shows, only from a list, capped — and every text
    cleaned as a search result's is (tooling.clean_free_text): it comes from the frame,
    which posts whatever it is made to, and the selection tool hands it to the model."""
    allowed = ("rank", "photo_id", "description", "photographer", "source", "license",
               "width", "height", "orientation", "url", "image_url", "attribution")
    out: list[dict] = []
    if not isinstance(items, list):
        return out
    for raw in items[:MAX_ITEMS]:
        if not isinstance(raw, dict) or not raw.get("photo_id"):
            continue
        item = {}
        for field in allowed:
            value = raw.get(field)
            if value in (None, ""):
                continue
            if isinstance(value, (int, float)):
                item[field] = value
            elif isinstance(value, str):
                text = tooling.clean_free_text(value, MAX_TEXT)
                if text:
                    item[field] = text
        item["rank"] = len(out) + 1                  # renumbered here: ranks are ours
        out.append(item)
    return out


def put(key: str, items, revision: int = 0, *, frame: str = "",
        now: float | None = None) -> dict:
    """Record a caller's selection. Returns what was stored.

    `revision` counts a frame's own writes, so it orders two posts that crossed on the
    wire. It says NOTHING across frames: a reloaded widget starts counting again at one,
    and a rule that read that as "older" would leave the reader looking at a fresh
    selection the server refuses to accept — which is exactly the failure this whole
    channel exists to end. So the guard applies only within one frame, and a write from
    a different frame is always the newer state.
    """
    stamp = now if now is not None else time.time()
    cleaned = _clean_items(items)
    entry = {"items": cleaned, "count": len(cleaned), "revision": int(revision or 0),
             "frame": str(frame or "")[:64], "at": stamp}
    with _LOCK:
        previous = _STORE.get(key)
        if (previous and entry["revision"]
                and previous.get("frame", "") == entry["frame"]
                and previous.get("revision", 0) > entry["revision"]):
            return previous
        _STORE[key] = entry
        _sweep(stamp)
    return entry


def note_grid(key: str, *, now: float | None = None) -> None:
    """A grid is on its way to this caller. Anything they picked before this moment was
    picked in a grid they are no longer looking at."""
    if not key:
        return
    stamp = now if now is not None else time.time()
    with _LOCK:
        _GRIDS[key] = stamp
        if len(_GRIDS) > MAX_KEYS:
            for k, _ in sorted(_GRIDS.items(), key=lambda kv: kv[1])[:len(_GRIDS) - MAX_KEYS]:
                _GRIDS.pop(k, None)


_EMPTY = {"items": [], "count": 0, "revision": 0, "frame": "", "at": 0.0, "stale": False}


def get(key: str, *, now: float | None = None) -> dict:
    """What this caller picked IN THE GRID THEY ARE LOOKING AT, or an empty selection.

    The second half of that sentence is the whole of this function: keyed on the person
    alone, a shortlist made in one conversation was served, with complete confidence, as
    the answer in the next one.

    Two things date a selection. It expires, like anything else here; and it is older
    than the last grid this caller was sent, which means it was made in a grid that has
    since been replaced. The second is the one that matters: a new search always draws
    a new grid, and a widget that cannot write — an old cached build, a host that drops
    `_meta` — then leaves the previous answer standing, which is exactly the failure.
    An empty answer is honest. A confident wrong one is not.
    """
    stamp = now if now is not None else time.time()
    with _LOCK:
        entry = _STORE.get(key)
        if not entry or stamp - entry["at"] > SELECTION_TTL:
            return dict(_EMPTY)
        drawn = _GRIDS.get(key, 0.0)
        # A second of slack: the grid that a selection belongs to is sent moments
        # before the frame renders it, and the two clocks are the same clock.
        if drawn and entry["at"] < drawn - 1.0:
            out = dict(_EMPTY)
            out["stale"] = True
            return out
        return dict(entry)


def clear(key: str) -> None:
    """Forget one caller's selection, grid stamp and views. The tests' reset between
    cases."""
    with _LOCK:
        _STORE.pop(key, None)
        _GRIDS.pop(key, None)
        for k in [k for k in _VIEWS if k.startswith(key + "|")]:
            _VIEWS.pop(k, None)


# ── The view, for a host that keeps none ─────────────────────────────────────
# A host without a store for its frames (VS Code: no `setWidgetState`) mounts the grid
# again from the answer at every turn of the conversation. The reader found the photos
# they liked unliked, the shape they filtered undone and the deck closed — and the
# fresh frame's first word emptied the selection they had just made, a moment before
# the model asked for it (measured, VS Code, 2026-10-01). So the grid keeps its view
# here instead: what it writes into the host's store where there is one (saveUi in
# widget.py), handed back to the next frame mounted from the same answer.
#
# Keyed by that answer's token (view_key), not by the person: an anonymous person is
# their address, and two people behind one address who ran the same search would
# otherwise read each other's grid. The host hands a remounted frame the very same
# answer, token included, and nobody else has it.
#
# Opaque to the server, which stores it as written and never reads it; bounded in size
# and number, and gone with the selection it goes with.
VIEW_TTL = SELECTION_TTL
VIEW_MAX = 96 * 1024
VIEW_MAX_KEYS = 1000
VIEW_SIG_MAX = 200
_VIEWS: dict[str, dict] = {}


def view_key(token: str) -> str:
    """Where the view of the answer that carried `token` is kept, or "" for a token
    that is not ours or has expired."""
    person = read_token(token)
    if not person:
        return ""
    return person + "|" + hashlib.sha256(token.encode()).hexdigest()[:24]


def put_view(key: str, sig: str, view, *, now: float | None = None) -> bool:
    """Keep the view a frame wrote over the answer `sig`. False when it is not an
    object, or too large once written."""
    if not key or not isinstance(sig, str) or not sig or len(sig) > VIEW_SIG_MAX:
        return False
    if not isinstance(view, dict):
        return False
    text = json.dumps(view, separators=(",", ":"), ensure_ascii=False)
    if len(text.encode()) > VIEW_MAX:
        return False
    stamp = now if now is not None else time.time()
    with _LOCK:
        _VIEWS[key + "|" + sig] = {"text": text, "at": stamp}
        dead = [k for k, v in _VIEWS.items() if stamp - v["at"] > VIEW_TTL]
        for k in dead:
            _VIEWS.pop(k, None)
        if len(_VIEWS) > VIEW_MAX_KEYS:              # oldest first
            for k, _ in sorted(_VIEWS.items(), key=lambda kv: kv[1]["at"])[:len(_VIEWS) - VIEW_MAX_KEYS]:
                _VIEWS.pop(k, None)
    return True


def get_view(key: str, sig: str, *, now: float | None = None):
    """The view last kept for this person over the answer `sig`, or None."""
    if not key or not isinstance(sig, str) or not sig:
        return None
    stamp = now if now is not None else time.time()
    with _LOCK:
        entry = _VIEWS.get(key + "|" + sig)
        if not entry or stamp - entry["at"] > VIEW_TTL:
            return None
        text = entry["text"]
    return json.loads(text)


# One switch, because a tool is surface: it is one more thing in every tools/list, one
# more thing a model can reach for at the wrong moment. Off, the selection still travels
# the two host channels the widget uses and the server still records it — only the
# reading tool disappears. Set PEXAFY_SELECTION_TOOL=0 to find out whether the host
# channel is enough.
TOOL_ENABLED = flag("PEXAFY_SELECTION_TOOL", True)

# The name a caller sees and calls, from the registry (keyed by the internal name).
SELECTED_TOOL = tooling.PUBLIC_TOOL_NAMES["get_selected_photos"]
SELECTED_TITLE = "Get the photos the person liked in the grid"

# ── What rides on an answer so the frame can write ───────────────────────────
SELECTION_META_KEY = "pexafy/selection"


def meta_for_result() -> dict | None:
    """The write capability, for the `_meta` half of a tool result, and the moment
    that dates this caller's previous selection (see `get`). None when the server does
    not know its public address or cannot name the caller: the widget then keeps to
    the two host channels."""
    if not PUBLIC_URL:
        return None
    key = key_for_caller()
    token = issue_token(key)
    if not token:
        return None
    note_grid(key)
    # `tool` is what lets the grid say LESS to the model: with a tool standing by, the
    # model context needs to name the selection, not carry it. Without one — the switch
    # is off — the context is the only channel there is, and the widget spells it out.
    return {"post_url": PUBLIC_URL + "/selection", "token": token,
            "tool": SELECTED_TOOL if TOOL_ENABLED else "",
            # Where the grid keeps its view in a host that keeps none (put_view).
            "view_url": PUBLIC_URL + "/view"}


# ── The tool the model calls ─────────────────────────────────────────────────
def _expiry_phrase() -> str:
    """SELECTION_TTL in words, so the description says what the store does."""
    minutes = max(1, round(SELECTION_TTL / 60))
    if minutes % 60:
        return f"{minutes} minute{'s' if minutes > 1 else ''}"
    hours = minutes // 60
    return "an hour" if hours == 1 else f"{hours} hours"


def selected_description(*, file_tool: bool = True) -> str:
    """The tool description, naming the file tool only when it is registered.

    Facts, not orders. What the tool returns and when the person's words call for it;
    where the selection lives, which is why this tool and not the conversation answers
    for it (a host that served a stale model context once had the model say the
    selection was unavailable); what `rank` is on the person's screen; and what an
    empty answer means. How to talk about the photographs is left to the model.
    """
    paragraphs = [
        "Returns the photographs the person liked (hearted) in the Pexafy results grid. Use it when the person refers to that selection rather than asking for new photos — for example “the images I selected”, “the ones I chose”, “my selection”, “the photos I hearted”, “the photos I liked”, “use the ones I picked”, “put my selection in the document”, “download the ones I chose”, or “write the article around my photos”.",
        "The selection is held by the grid and the server, not by the conversation: this tool is how it is read, and search results are not the selection. It takes no arguments and returns every liked photograph with its rank, photo_id, photographer, source library, licence, pixel size, orientation, description, page URL and credit line, and `selection_count`, the number of them.",
        "`rank` is the person's order, #1 first: the order the photos were liked in, unless the person rearranged them by dragging in the grid. Where the grid is rendered, that number is drawn on each liked photograph, so it is the number the person sees and names them by (“#2”). The `rank` of a search result is only its position in that answer and is not drawn on the grid.",
        f"An empty selection means nothing is liked in the grid currently on screen; a photograph is liked with the heart on it. A selection expires {_expiry_phrase()} after its last change, and a new search replaces the grid it belongs to.",
    ]
    if file_tool:
        paragraphs.insert(2, f"To get one of them as an image file, pass its photo_id to {tooling.PUBLIC_TOOL_NAMES['get_photo_file']}.")
    return "\n\n".join(paragraphs)


# The full text, as production serves it.
SELECTED_DESCRIPTION = selected_description()


# What an empty answer adds: the store holds only what reached it. A grid that cannot
# post here — ChatGPT, 2026-10-03: a connector kept the content security policy it was
# created with, and the browser refused every post — carries the selection in its own
# context instead (widget.py, selPostFailed). The tool read 0 for two photos liked, and
# the model, holding both answers, chose this one and said the selection had expired.
UNSENT_NOTE = (
    " If the grid's own context in this conversation lists liked photographs, the grid "
    "could not send them here, and that list is the person's selection."
)


async def get_selected_photos() -> dict:
    """The photographs the person selected in the Pexafy grid."""
    key = key_for_caller()
    entry = get(key) if key else {"items": [], "count": 0, "revision": 0}
    items = entry["items"]
    logger.info("selection read count=%d", len(items))
    from . import hosts

    if items and hosts.renders_grid():
        # Where the person sees the grid, the model reads what they liked as it reads a
        # search there (previews.grid_summary): without the image links, which the grid
        # posts as each photo's 1280w preview and which a model can show again in the
        # chat. The file tool still hands one over, with its link, when it is asked for.
        items = [{k: v for k, v in item.items() if k != "image_url"} for item in items]
    return {
        "success": True,
        "selection_count": len(items),
        "selected_photos": items,
        "note": (
            "The person liked these in the Pexafy grid, in this order — #1 first, the order "
            "they liked them in or the one they rearranged them into — and each number is "
            "drawn on its photograph on their screen, so "
            "those are the numbers they name them by. This is the whole selection."
            if items else
            "Nothing liked in the grid the person is looking at has reached Pexafy. Anything "
            "they liked earlier has expired, or belongs to a grid a later search replaced, so "
            "it is not their selection any more." + UNSENT_NOTE
            if entry.get("stale") else
            # Both cases the store cannot tell apart: nothing was ever liked, or it was
            # and the entry outlived SELECTION_TTL. "Has not liked any photograph yet"
            # was false to somebody who liked three, seventy minutes earlier.
            "The selection was read and is empty, which is not an access problem: nothing "
            "is liked in the Pexafy grid, or the selection's last change was more than "
            f"{_expiry_phrase()} ago and it has expired. Where the grid is shown, a "
            "photograph is liked with the heart on it; any photograph can also be used by "
            "its `photo_id`." + UNSENT_NOTE
        ),
    }
