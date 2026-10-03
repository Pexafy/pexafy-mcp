"""Every text a host hands the model, held to the rules of both directories.

OpenAI's app review and Anthropic's connector directory refuse the same family of
sentences: a trigger on a need the model infers on its own rather than on what the user
asks, a call nobody asked for, a word against another tool or service, an order about
how to behave that is not about using the tool, and a server speaking as the assistant.
The rules, quoted:

  OpenAI (developers.openai.com/plugins/app-guidelines)
    "Descriptions must not recommend overly broad triggering beyond the explicit user
    intent and purpose the plugin fulfills."
    "Descriptions must not favor or disparage other plugins or services or attempt to
    influence the model to select them over another plugin's tools."
    "Do not insert unrelated content, attempt to redirect the interaction […]"
    "We cannot approve plugins that primarily function as unofficial connectors to
    third-party services, including pass-through intermediary software layers."
  Anthropic (claude.com/docs/connectors/building/review-criteria, directory policy)
    "Describe what the tool does, and don't tell Claude how to behave."
    2.D "must not intentionally call or coerce Claude into calling other external
    software, tools […] unless requested and intended by a user."
    2.E "must not attempt to interfere with Claude calling tools from other software."
    2.B "Descriptions must not include unexpected functionality or promise undelivered
    features."

Each formula in FORBIDDEN crossed one of those lines once, in a text this server served.
This file fails if one comes back anywhere the model reads: the instructions, a tool
(title, description, every parameter and output field, `_meta`), the prompt, the grid
resource, a message sent at run time (plan limits, the budget notice, the selection's
note, the guards, the tool errors), the context the grid pushes to the model, or the
skill. It also pins the other half: the triggers the user states are all still there,
and the one case the model sees coming is an offer that waits for the user.

What is allowed, on purpose:
  - "generate" in a statement of what Pexafy does NOT do ("does not generate, edit or
    upscale images"): both directories ask for the tool's limits to be stated. Only
    "generated", the adjective every comparison with image generation used ("instead of
    a generated image", "never answer with a generated picture"), is refused.
  - an order about the tool's own use ("Do not retry the search", "leave it unset",
    "Omit this parameter unless…"): that is how the tool is used, not how the model
    behaves.
  - "When NOT to use Pexafy": a heading that narrows the server's use.
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import MiddlewareContext

from pexafy_mcp import (budget, guards, limits, linking, previews, selection, server,
                        tooling, widget)

SKILL = Path(__file__).resolve().parent.parent / "skills" / "find-stock-photos" / "SKILL.md"

# pattern → why it is refused. Case-sensitive where the capitals are the point.
FORBIDDEN: dict[str, str] = {
    # A trigger on the model's own initiative, not on what the user asks or says.
    r"(?i)\bor you\b": "\"the user or you need…\": a need the model infers (OpenAI: beyond the explicit user intent; Anthropic 2.D)",
    r"(?i)\bon your own\b": "\"when you determine, on your own…\": an unrequested call",
    r"(?i)\byour own answer\b": "\"or your own answer wanting a picture\": an unrequested call",
    r"(?i)\bexplicitly or implicitly\b": "an implicit need is not the explicit user intent",
    r"(?i)\bbenefits? from\b": "\"content that benefits from photographs\": a need nobody stated",
    r"(?i)\bproactively\b": "a call before the user asks",
    r"(?i)whenever a picture is what the request is about": "covers \"describe this image\"",
    # A word against another tool or service.
    r"(?i)\bgenerated\b": "a comparison with image generation, someone else's tool (OpenAI fair play; Anthropic 2.E)",
    r"(?i)watermark": "disparages paid libraries, and reads as licence circumvention",
    r"(?i)\bpaid (?:or watermarked )?stock\b": "disparages paid libraries",
    r"(?i)rather than to tags": "a comparison with tag search",
    r"(?i)what a list of tags cannot": "a comparison with tag search",
    # A relay of queries to other services, rather than Pexafy's own index.
    r"(?i)\bin one query\b": "reads as a pass-through connector to Unsplash, Pexels, Pixabay",
    # Orders shouted at the model.
    r"\b(?:CALL|BEFORE|FILE|NEVER|ALWAYS|MUST|ONE|ONLY|IMMEDIATELY)\b": "capitals of an injected order",
    r"(?i)\bimmediately\b": "\"CALL connect_account immediately\": redirects the interaction",
    # Orders about how to behave, beyond the use of the tool itself.
    r"(?i)\bbefore saying\b": "\"Call it before saying…\": how to answer, not how to use the tool",
    r"(?i)never report it as an error": "how to report a result",
    r"(?i)never call the tool again": "stated as a fact instead: calling again returns the same request",
    r"(?i)tell the person the connection": "what to tell the person about a connection",
    r"(?i)read it before answering": "a schema field that orders the model",
    r"(?i)the one to read out": "a schema field that orders the model",
    r"(?i)never refer to one by position": "a hidden order in the grid's context",
    r"(?i)unavailable to you": "\"never say it is unavailable to you\": a hidden order",
    r"(?i)never renumber": "how to talk about the selection",
    r"(?i)never assume there is only one": "how to read the selection",
    r"(?i)ask them to tap the heart": "what to ask the person",
    r"(?i)do not list them again": "how to write the answer",
    r"(?i)say what you found": "how to write the answer",
    r"(?i)give them this link": "a link handed to the model to pass on",
    # The server speaking as the assistant.
    r"\bI can't\b": "the server speaking as the assistant",
    r"\bI cannot\b": "the server speaking as the assistant",
    r"(?i)\bask me\b": "the server speaking as the assistant",
    # Promises the tool cannot keep everywhere (Anthropic 2.B).
    r"(?i)\ban attached image\b": "only a host that passes uploads to tools can send one",
    r"(?i)\bthe user attached\b": "only a host that passes uploads to tools can send one",
    r"(?i)millions of royalty-free": "a licence promise; each result names its licence",
    r"(?i)every photograph is free to use": "a licence promise; each result names its licence",
}


def _violations(where: str, text: str) -> list[str]:
    return [f"{where}: {re.search(p, text).group(0)!r} — {why}"
            for p, why in FORBIDDEN.items() if re.search(p, text)]


def _strings(node, path: str = ""):
    """Every string inside a JSON-like value, with where it sits."""
    if isinstance(node, str):
        yield path, node
    elif isinstance(node, dict):
        for key, value in node.items():
            yield from _strings(value, f"{path}.{key}" if path else str(key))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _strings(value, f"{path}[{i}]")


async def _surface(monkeypatch, *, selection_tool: bool = True, account_linking: bool = True,
                   grid: bool = True):
    """What `initialize`, `tools/list`, `prompts/list` and `resources/list` send, with
    the grid on (production) or off, as (where, text) pairs."""
    monkeypatch.setattr(previews, "PREVIEWS_AVAILABLE", grid)
    monkeypatch.setattr(selection, "TOOL_ENABLED", selection_tool)
    monkeypatch.setattr(linking, "ENABLED", account_linking)
    async with Client(server.build_server()) as client:
        found = [("instructions", client.initialize_result.instructions or "")]
        for kind, items in (("tool", await client.list_tools()),
                            ("prompt", await client.list_prompts()),
                            ("resource", await client.list_resources())):
            for item in items:
                dumped = item.model_dump(mode="json", by_alias=True, exclude_none=True)
                found += [(f"{kind} {getattr(item, 'name', '')}: {where}", text)
                          for where, text in _strings(dumped)]
    return found


async def _runtime_messages(monkeypatch) -> list[tuple[str, str]]:
    """Every sentence the server composes while running, in each of its variants."""
    out: list[tuple[str, str]] = []

    # Plan limits.
    for ctx in ({"plan_label": "Free", "max": 1}, {"plan_label": "Starter", "max": 3}, {}):
        out.append(("limits.key_limit_message", limits.key_limit_message(ctx)))
    for options in ("", "https://pexafy.com/mcp/limits/"):
        monkeypatch.setattr(budget, "OPTIONS_URL", options)
        out.append(("limits.monthly_quota_message", await limits.monthly_quota_message("free", 5000)))
        out.append(("limits.monthly_quota_message", await limits.monthly_quota_message("")))
    for enabled in (True, False):
        monkeypatch.setattr(linking, "ENABLED", enabled)
        for retry in (3600, 7200, None):
            for host_can_connect in (True, False):   # ChatGPT, then any other host
                out.append(("limits.daily_quota_message",
                            await limits.daily_quota_message(
                                "anonymous", 100, retry, host_can_connect=host_can_connect)))
    monkeypatch.setattr(linking, "ENABLED", True)
    for retry in (30, None):
        out.append(("limits.rate_limit_message", await limits.rate_limit_message("free", 20, retry)))
        out.append(("limits.rate_limit_message",
                    await limits.rate_limit_message("anonymous", 30, retry)))

    # The budget notice, the wall and the account block, for each kind of caller.
    anonymous_day = {"X-Plan": "anonymous", "X-Daily-Quota-Limit": "100"}
    signed_month = {"X-Plan": "free", "X-Quota-Limit": "5000"}
    for headers in ({**anonymous_day, "X-Daily-Quota-Remaining": "5"},
                    {**anonymous_day, "X-Daily-Quota-Remaining": "1"},
                    {**anonymous_day, "X-Daily-Quota-Remaining": "0", "Retry-After": "3600"},
                    {**signed_month, "X-Quota-Remaining": "100"},
                    {**signed_month, "X-Quota-Remaining": "0"},
                    {"X-Plan": "anonymous", "X-Quota-Limit": "100", "X-Quota-Remaining": "0"}):
        for name, block in (("budget.read", budget.read(headers)),
                            ("budget.wall", budget.wall(headers)),
                            ("budget.account", budget.account(headers))):
            out += [(f"{name}.{where}", text) for where, text in _strings(block or {})]

    # connect_account's answers.
    for name in ("CONNECT_PROMPT", "CONNECT_REQUEST_REPORT", "ALREADY_CONNECTED", "NOT_CONNECTED"):
        out.append((f"linking.{name}", getattr(linking, name)))

    # The selection's note: liked photos, a selection a later search replaced, an empty
    # or expired one.
    monkeypatch.setattr(selection, "key_for_caller", lambda: "k:compliance")
    selection.clear("k:compliance")
    out.append(("selection.note (empty)", (await selection.get_selected_photos())["note"]))
    selection.put("k:compliance", [{"photo_id": "p1"}, {"photo_id": "p2"}], 1, frame="f")
    out.append(("selection.note (liked)", (await selection.get_selected_photos())["note"]))
    selection.put("k:compliance", [{"photo_id": "p1"}], 2, frame="f", now=time.time() - 10)
    selection.note_grid("k:compliance")
    out.append(("selection.note (replaced)", (await selection.get_selected_photos())["note"]))
    selection.clear("k:compliance")

    # The guards' refusals.
    by_image = tooling.PUBLIC_TOOL_NAMES["search_photos_by_image"]
    for tool, arguments in (
        (tooling.PUBLIC_TOOL_NAMES["search_photos"], {}),
        (tooling.PUBLIC_TOOL_NAMES["search_photos"], {tooling.PUBLIC_QUERY_PARAM: "x" * 400}),
        (tooling.PUBLIC_TOOL_NAMES["search_photos"],
         {tooling.PUBLIC_QUERY_PARAM: "a cat", tooling.PUBLIC_ORIENTATION_PARAM: ["wide"]}),
        (by_image, {"photo_id": "6"}),
        (by_image, {"photo_id": "not-a-uuid"}),
        (tooling.PUBLIC_TOOL_NAMES["get_photo_file"], {"photo_id": "#2"}),
    ):
        async def call_next(_context):
            raise AssertionError("the guard let a malformed call through")

        context = MiddlewareContext(message=SimpleNamespace(name=tool, arguments=dict(arguments)))
        with pytest.raises(ToolError) as refused:
            await guards.GuardPhotoId().on_call_tool(context, call_next)
        out.append((f"guards ({tool})", str(refused.value)))

    # The tools' own errors.
    for name in ("_IMG_BAD_URL", "_IMG_UNREACHABLE", "_IMG_NOT_AN_IMAGE", "_IMG_B64_FORMAT"):
        out.append((f"server.{name}", getattr(server, name)))
    out.append(("server._img_too_large()", server._img_too_large()))
    for arguments in ({}, {"photo_id": tooling.PHOTO_ID_EXAMPLE, "image_url": "https://x/y.jpg"}):
        with pytest.raises(ToolError) as refused:
            await server.search_photos_by_image(**arguments)
        out.append(("server.search_photos_by_image", str(refused.value)))
    with pytest.raises(ToolError) as refused:
        await server.get_photo_file("")
    out.append(("server.get_photo_file", str(refused.value)))
    failed = httpx.Response(503, json={"error": {"message": "Search is down."}},
                            request=httpx.Request("GET", "http://api/api/v1/search/photos"))
    with pytest.raises(ToolError) as refused:
        await server._surface_search_errors(failed)
    out.append(("server._surface_search_errors", str(refused.value)))
    return out


def _js_literals(source: str) -> list[str]:
    """The string literals of a piece of the widget's JavaScript, comments left out."""
    code = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    code = re.sub(r"(?m)^\s*//.*$", "", code)
    return [a or b for a, b in re.findall(r'"((?:[^"\\\n]|\\.)*)"|\'((?:[^\'\\\n]|\\.)*)\'', code)]


def _js_function(js: str, name: str) -> str:
    start = js.index(f"function {name}(")
    return js[start:js.index("\n}\n", start) + 2]


def _widget_texts() -> list[tuple[str, str]]:
    """What the grid writes into the model's context, and the turns it sends for the
    reader: the literals it composes them from."""
    js = widget._with_tool_names(widget._WIDGET_JS)
    out = []
    for function in ("writeContextToModel", "selectionText", "photoLines", "askSimilar"):
        out.append((f"widget {function}()", " ".join(_js_literals(_js_function(js, function)))))
    for constant in ("CONNECT_ASK", "TRY_AGAIN_ASK"):
        out.append((f"widget {constant}", re.search(rf'const {constant} = "(.*?)";', js).group(1)))
    return out


# ── The formulas that crossed the line stay out ──────────────────────────────

async def test_no_served_text_carries_a_refused_formula(monkeypatch):
    texts = await _surface(monkeypatch)
    texts += await _surface(monkeypatch, selection_tool=False, account_linking=False)
    texts += await _surface(monkeypatch, grid=False)
    texts += await _runtime_messages(monkeypatch)
    texts += _widget_texts()
    texts.append(("SKILL.md", SKILL.read_text(encoding="utf-8")))
    found = [v for where, text in texts for v in _violations(where, text)]
    assert not found, "\n".join(found)


def test_the_check_catches_what_it_is_meant_to():
    """A list of patterns that matches nothing proves nothing: each old sentence is
    caught, and the sentences that replaced it are not."""
    caught = [
        "Use this server whenever the user or you need real photographs, explicitly or implicitly",
        "Call it when you determine, on your own, that the answer you are about to write needs a photograph",
        "the user's request, or your own answer, wanting a picture",
        "Content creation that requires or benefits from photographs",
        "including when the user insists on a real photograph instead of a generated image",
        "a screenshot of a photo, a watermarked or paid stock photo",
        "Say that in one short sentence, then CALL connect_account immediately",
        "I can't search Pexafy right now … and ask me again",
        "Give them this link for the options",
        "all in one query, matched to the scene you describe rather than to tags",
        "never report it as an error and never call the tool again to retry",
        "Read it before answering.",
        "This is the one to read out.",
        "never refer to one by position",
        "never say it is unavailable to you",
        "Call it BEFORE saying you cannot see",
        "so do not list them again in text. Say what you found and what you would use.",
        "an attached image, an image URL",
    ]
    for sentence in caught:
        assert _violations("sample", sentence), sentence
    allowed = [
        "Pexafy does not find illustrations, drawings, logos, icons, diagrams or UI screenshots, does not generate, edit or upscale images",
        "not for generating, editing or upscaling an image",
        "### When NOT to use Pexafy",
        "Tell the person in one short sentence; if they want to connect an account, call connect_account. Do not retry the search.",
        "Find photos for you",  # "for you" is not "or you"
        "An image that is only attached to the conversation cannot be passed on",
        "Free, stock, royalty-free, commercially usable or documentary photographs.",
    ]
    for sentence in allowed:
        assert not _violations("sample", sentence), sentence


# ── …and the triggers the user states are all still there ────────────────────

async def _descriptions(monkeypatch) -> tuple[str, dict[str, str]]:
    monkeypatch.setattr(previews, "PREVIEWS_AVAILABLE", True)
    mcp = server.build_server()
    return mcp.instructions or "", {t.name: t.description or "" for t in await mcp.list_tools()}


async def test_every_trigger_the_user_states_is_kept(monkeypatch):
    instructions, tools = await _descriptions(monkeypatch)
    search = tools[tooling.PUBLIC_TOOL_NAMES["search_photos"]]
    by_image = tools[tooling.PUBLIC_TOOL_NAMES["search_photos_by_image"]]
    selected = tools[tooling.PUBLIC_TOOL_NAMES["get_selected_photos"]]
    connect = tools[linking.CONNECT_TOOL]
    skill = SKILL.read_text(encoding="utf-8")
    expected = {
        "instructions": (instructions, (
            "asks for real photographs", "states a need that photographs fill",
            "photos or pictures", "landing pages", "backgrounds", "mood boards",
            "write an article with pictures", "royalty-free", "commercially usable",
            "documentary", "A real photograph, when the user asks for one",
            "Free alternatives to a picture", "any language")),
        "search_photos": (search, (
            "find me photos of", "show me images of", "I need a picture of",
            "do you have a photo of", "where can I find free photos of", "header image",
            "hero image", "backgrounds",
            "I need something to illustrate this", "write an article with pictures",
            "a deck with images", "a photo of a white mug", "a train in Japan",
            "in any language", "real or documentary photographs", "commercially usable")),
        "search_photos_by_image": (by_image, (
            "find images like this", "something similar to this photo",
            "find a free alternative to this image", "I need this but royalty-free",
            "photos in this style", "more like the second one", "search by image")),
        "get_grid_selected_photos": (selected, (
            "the images I selected", "the ones I chose", "my selection",
            "the photos I liked", "put my selection in the document",
            "download the ones I chose", "write the article around my photos")),
        "connect_account": (connect, ("connect, link or sign in", "account button")),
        "SKILL.md": (skill, (
            "asks for real photographs", "write an article with pictures",
            "a photo of a white mug", "any language", "free alternatives to a picture",
            "background", "show me images of")),
    }
    for where, (text, triggers) in expected.items():
        for trigger in triggers:
            assert trigger in text, (where, trigger)


async def test_the_one_case_the_model_sees_coming_is_an_offer(monkeypatch):
    """Somebody writing an article has not asked for photographs. The model may offer to
    look, and searches once they accept — the call follows the user's yes, which is
    what both directories require of a call."""
    instructions, tools = await _descriptions(monkeypatch)
    search = tools[tooling.PUBLIC_TOOL_NAMES["search_photos"]]
    assert "you may offer to look for some; call it only once they accept" in search
    skill = SKILL.read_text(encoding="utf-8")
    assert "you can offer to find some with Pexafy, and search once they say yes" in skill
    # And the rest of the time, nothing is added unasked.
    assert "Do not use Pexafy to add photographs the user did not ask for." in instructions
    assert "not to add photographs the user did not ask for" in skill
    # The user's own words come first.
    assert "The user's explicit instructions come first" in skill


async def test_the_texts_say_what_the_tools_really_do(monkeypatch):
    """Anthropic 2.B: no promise the tool cannot keep in every host."""
    instructions, tools = await _descriptions(monkeypatch)
    by_image = tools[tooling.PUBLIC_TOOL_NAMES["search_photos_by_image"]]
    # An uploaded image reaches the tool only through a host that passes it.
    assert "the host passes" in instructions and "the host passes" in by_image
    # A picture that is neither linked nor passed is not promised to the by-image search,
    # and the route that does work, its scene in words, is written down everywhere.
    words = tooling.PUBLIC_TOOL_NAMES["search_photos"]
    assert f"its scene can be searched in words with `{words}`" in by_image
    assert "by image when it is linked or passed to the tool, in words otherwise" in instructions
    skill = SKILL.read_text(encoding="utf-8")
    assert "is searched in words instead" in skill
    assert "ask the user for a link" not in skill
    # A picture pasted into the conversation is an upload that ChatGPT passes to the
    # tool: only some hosts leave it to a search in words.
    assert "on some hosts a picture pasted into the conversation" in skill
    # The image leaves the conversation, and says so.
    assert "sent to Pexafy only to run the search; it is not stored" in by_image
    # The libraries are where Pexafy's own index comes from, and licences are per result.
    search = tools[tooling.PUBLIC_TOOL_NAMES["search_photos"]]
    assert "Pexafy searches its own index" in instructions and "Pexafy's own index" in search
    assert "its licence" in search and "its licence" in by_image
    # connect_account says what happens where the host does not show a prompt.
    connect = tools[linking.CONNECT_TOOL]
    assert "other hosts show only the result's text" in connect
    assert "Calling it again returns the same request" in connect
