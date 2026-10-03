"""What an answer carries to the model, and what it no longer does.

OpenAI's app guidelines, under Privacy: "Response minimization: […] Do not include
diagnostic, telemetry, or internal identifiers—such as session IDs, trace IDs, request
IDs, timestamps, or logging metadata—unless they are strictly required to fulfill the
user's query", and "only data that is directly relevant to the user's request". The
review lists a result that carries them as a reason for rejection.

So, off the model's half of every answer:

  meta.request_id, meta.took_ms   the API's telemetry
  error.request_id                the same id, in the error object
  account.plan, budget.plan       the plan's name — nothing draws it
  selection_revision              a counter of the frame's own writes
  cta                             the grid's link to the website — moved to `_meta`
  account                         who is asking, the way to sign in — moved to `_meta`
  budget.short/title/detail,      the allowance panel's wording and its button — moved
  budget.cta_label/account_url    to `_meta`; `budget` keeps its numbers and `message`

Each is checked on the wire — a real client against the real server, the API replaced
in memory — in `structuredContent` AND in the text block, which a host builds from the
same body and which is what most models actually read. A field removed from one and
not the other would still be sent.

    pytest tests/test_minimisation.py -v
"""
from __future__ import annotations

import json

import httpx
import pytest
from fastmcp import Client

from pexafy_mcp import budget, origin, selection, server, tooling

PHOTO_ID = "0199a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b"

# A search answer as the API writes it, telemetry included.
_PAYLOAD = {
    "success": True,
    "data": [{"photo_id": PHOTO_ID, "width": 4000, "height": 3000,
              "urls": {"regular": "https://images.example/1.jpg"}}],
    "meta": {"request_id": "6ecfa8dc-0000-4000-8000-000000000000", "took_ms": 86.58},
    "pagination": {"has_more": False, "next_cursor": None},
    "error": None,
}

# An allowance nearly spent, so that `budget` rides on the answer too, and `account`.
_HEADERS = {"X-Plan": "starter", "X-Quota-Limit": "100", "X-Quota-Remaining": "5"}

_GONE = ("request_id", "took_ms", '"plan"', '"cta"', '"meta"',
         '"account"', "sign_in_url", "account_url", "cta_label", "home_url")

# What the grid draws in its account corner and nothing else reads.
_GRID_ONLY_BUDGET = ("short", "title", "detail", "cta_label", "account_url",
                     # What the grid says the panel with in other languages (budgetWords).
                     "reset_hours", "free_account", "wall")


@pytest.fixture
def api(monkeypatch):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_PAYLOAD, headers=_HEADERS)

    monkeypatch.setattr(server.client, "_transport", httpx.MockTransport(handler), raising=False)
    return seen


def _text(result) -> str:
    return "".join(getattr(block, "text", "") or "" for block in result.content)


async def _call(tool: str, arguments: dict):
    async with Client(server.build_server()) as client:
        return await client.call_tool(tool, arguments)


@pytest.mark.parametrize(("tool", "arguments", "url"), [
    (tooling.PUBLIC_TOOL_NAMES["search_photos"], {"english_search_sentence": "a white mug"},
     "/?q=a+white+mug"),
    (tooling.PUBLIC_TOOL_NAMES["search_photos_by_image"], {"photo_id": PHOTO_ID},
     f"/photos/{PHOTO_ID}/"),
])
async def test_an_answer_carries_no_telemetry_and_no_plan(api, tool, arguments, url):
    result = await _call(tool, arguments)
    assert api, "no request reached the API"

    sc = result.structured_content
    assert "meta" not in sc and "pagination" not in sc and "cta" not in sc
    assert "account" not in sc
    assert "plan" not in sc["budget"]
    assert sc["budget"]["remaining"] == 5            # the notice itself is still there
    # The model's half of the notice: its numbers and its sentence, nothing to draw.
    assert set(sc["budget"]) == set(budget.MODEL_FIELDS)
    text = _text(result)
    for gone in _GONE + tuple(f'"{name}"' for name in _GRID_ONLY_BUDGET):
        assert gone not in text, gone
    # The photograph is untouched.
    assert sc["data"][0]["photo_id"] == PHOTO_ID

    # The link the grid draws now rides on `_meta`, which the model does not read.
    meta = result.meta or {}
    cta = meta[origin.CTA_META_KEY]
    assert cta["url"] == origin.WEB_URL + url
    assert cta["label"] == "Open in Pexafy"
    # …and so does the account corner: who is asking, and the panel's wording.
    grid = meta[budget.ACCOUNT_META_KEY]
    assert grid["account"] == {"state": "signed_in", "label": "Signed in",
                               "home_url": budget.HOME_URL}
    assert set(grid["budget"]) <= set(_GRID_ONLY_BUDGET)
    assert grid["budget"]["short"] == "5 searches left this month"


async def test_the_wall_splits_the_same_way(monkeypatch):
    """A spent allowance comes back as an empty answer the grid draws as a wall
    (server._wall_as_grid). The model reads that the searches are used up and when they
    return; the wall's title, its second line and its sign-in button are the grid's."""
    refusal = {"success": False, "data": None,
               "error": {"code": "DAILY_QUOTA_EXCEEDED", "message": "nope"}}
    headers = {"X-Plan": "anonymous", "X-Daily-Quota-Limit": "100",
               "X-Daily-Quota-Remaining": "0", "Retry-After": "25200"}
    monkeypatch.setattr(server.client, "_transport", httpx.MockTransport(
        lambda request: httpx.Response(429, json=refusal, headers=headers)), raising=False)
    result = await _call(tooling.PUBLIC_TOOL_NAMES["search_photos"],
                         {"english_search_sentence": "a white mug"})

    sc = result.structured_content
    assert sc["data"] == [] and "used up" in sc["notice"]
    assert "account" not in sc
    assert set(sc["budget"]) == set(budget.MODEL_FIELDS)
    assert sc["budget"]["state"] == "exhausted"
    text = _text(result)
    for gone in _GONE + tuple(f'"{name}"' for name in _GRID_ONLY_BUDGET):
        assert gone not in text, gone

    grid = (result.meta or {})[budget.ACCOUNT_META_KEY]
    assert grid["account"]["state"] == "anonymous"
    assert grid["account"]["sign_in_url"] == budget.SIGN_IN_URL
    assert grid["budget"]["title"] == "Today's free searches are used up"
    assert grid["budget"]["cta_label"] == "Sign in"
    assert grid["budget"]["account_url"] == budget.SIGN_IN_URL


def test_the_account_corner_is_only_on_the_answers_a_grid_reads():
    """The hook learns who is asking on every call that reaches the API, the file tool's
    included; only the searches and the connect tool's probe carry it to a frame."""
    assert tooling.PUBLIC_TOOL_NAMES["get_photo_file"] not in origin.ACCOUNT_TOOLS
    assert origin.ACCOUNT_TOOLS == {tooling.PUBLIC_TOOL_NAMES[n] for n in
                                    ("search_photos", "search_photos_by_image",
                                     "connect_account")}


def test_outside_a_tool_call_nobody_collects_the_grid_s_half():
    """A hook run on its own (a unit call) has nowhere to put it, and must not fail."""
    budget.keep_for_grid(account={"state": "anonymous"})       # no holder: dropped
    with budget.collect_for_grid() as grid:
        budget.keep_for_grid(account={"state": "anonymous"}, budget=None)
        with budget.collect_for_grid() as inner:               # one per call
            budget.keep_for_grid(budget={"short": "1 search left today"})
    assert grid == {"account": {"state": "anonymous"}}
    assert inner == {"budget": {"short": "1 search left today"}}
    assert budget.for_model(None) is None and budget.for_grid(None) is None
    assert budget.for_grid({"state": "warning", "remaining": 3}) is None


async def test_the_request_id_leaves_an_error_object_too(monkeypatch):
    payload = dict(_PAYLOAD, error={"code": "X", "message": "m", "request_id": "r-1"})
    monkeypatch.setattr(server.client, "_transport", httpx.MockTransport(
        lambda request: httpx.Response(200, json=payload)), raising=False)
    result = await _call(tooling.PUBLIC_TOOL_NAMES["search_photos"], {"english_search_sentence": "cats"})
    assert result.structured_content["error"] == {"code": "X", "message": "m"}
    assert "r-1" not in _text(result)


async def test_the_declared_schemas_promise_none_of_it(monkeypatch):
    """Declared and sent are the same thing: a schema that still announced `request_id`
    would describe — and invite — a field no answer carries. With the grid on, so the
    selection tool is there too."""
    from pexafy_mcp import previews

    monkeypatch.setattr(previews, "PREVIEWS_AVAILABLE", True)
    tools = {t.name: t for t in await server.build_server().list_tools()}
    for name in (tooling.PUBLIC_TOOL_NAMES["search_photos"],
                 tooling.PUBLIC_TOOL_NAMES["search_photos_by_image"]):
        schema = tools[name].to_mcp_tool().outputSchema
        dump = json.dumps(schema)
        for gone in ("request_id", "took_ms", '"plan"', '"cta"', '"account"',
                     "sign_in_url", "account_url", "cta_label", "home_url"):
            assert gone not in dump, (name, gone)
        props = schema["properties"]
        assert "meta" not in props and "cta" not in props and "account" not in props
        # The rest of the envelope is still declared, and `budget` declares exactly
        # the half the model is sent.
        for kept in ("success", "data", "error", "budget", "notice"):
            assert kept in props, (name, kept)
        assert set(props["budget"]["properties"]) == set(budget.MODEL_FIELDS)
    selected = tools[selection.SELECTED_TOOL].to_mcp_tool().outputSchema
    assert "selection_revision" not in selected["properties"]
    assert "selection_complete" not in selected["properties"]
    assert set(selected["required"]) <= set(selected["properties"])


async def test_the_selection_answer_carries_no_revision():
    """`revision` orders one frame's own writes to the store; it says nothing to a
    model, which reads `selection_count` and the photographs."""
    key = "k:test-minimisation"
    selection.put(key, [{"photo_id": PHOTO_ID}], revision=7, frame="f1")
    try:
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(selection, "key_for_caller", lambda: key)
            answer = await selection.get_selected_photos()
    finally:
        selection.clear(key)
    assert "selection_revision" not in answer
    # Nor a constant: `selection_complete` was always true, and said nothing.
    assert "selection_complete" not in answer
    assert set(answer) == {"success", "selection_count", "selected_photos", "note"}
    assert answer["selection_count"] == 1
    assert answer["selected_photos"][0]["photo_id"] == PHOTO_ID


def test_the_grid_context_gives_the_model_no_internal_counter():
    """What the grid pushes to the model (`updateModelContext`) carries the state only.
    The frame's write counter goes to the server and to `privateContent`, never here."""
    from pexafy_mcp import widget

    js = widget._WIDGET_JS
    block = js[js.index("app.updateModelContext({"):]
    block = block[:block.index("});")]
    assert "selection_revision" not in block
    assert "selection_count" in block and "selected_photo_ids" in block
