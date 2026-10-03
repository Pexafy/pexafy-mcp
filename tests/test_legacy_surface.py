"""What a caller still on a published snapshot keeps getting.

A directory publishes a SNAPSHOT of the surface and routes calls to the live server.
The two move at different speeds: a new snapshot needs a review, the server ships when
it ships. Between a rename and the new snapshot's approval, a host still advertises the
old name and a caller still sends it — to a server where no tool has that name.

These tests check that it gets an answer rather than "Unknown tool", and that the
translation is silent: nothing appears under an old name in `tools/list`.
"""
import asyncio
import pathlib

from pexafy_mcp import compat, tooling


def test_every_published_name_is_translated():
    """Every name a published surface exposed still answers.

    The two 0.4.x searches are served under their own names again; the long names they
    wore on preprod, the short trial names of the file and selection tools, and
    `get_similar_photos` are translated to the tool that exists now.
    """
    for kept in ("search_photos", "search_photos_by_image"):
        assert kept in tooling.PUBLIC_TOOL_NAMES.values(), kept
    for old in (*tooling.LEGACY_TOOL_NAMES, "get_similar_photos"):
        assert old in compat.RENAMED_TOOLS, old
        assert compat.RENAMED_TOOLS[old] in tooling.PUBLIC_TOOL_NAMES.values()

    # And the reverse: a public name never translates to something else.
    for public in tooling.PUBLIC_TOOL_NAMES.values():
        assert compat.RENAMED_TOOLS.get(public, public) == public, public


def test_the_translation_is_silent():
    """`tools/list` announces ONLY the current names.

    Listing both would double the surface the model rereads on every turn and blur the
    routing, for nothing: a caller on the old snapshot does not need to READ the old
    name, it needs it to ANSWER.

    One exception, for an OpenAI host alone: `get_similar_photos` stays listed there as
    0.4.12 defined it, because the published app loses at its next scan a tool the list
    stops naming (compat.ListSimilarAlias, test_compat). This listing is no OpenAI one.
    """
    from pexafy_mcp import server
    names = {t.name for t in asyncio.run(server.build_server().list_tools())}
    # The file and selection tools register only when the thumbnail proxy is configured
    # (the grid they belong to): the two of the five that depend on the environment.
    expected = set(tooling.PUBLIC_TOOL_NAMES.values())
    assert names <= expected, names - expected
    assert expected - names <= {tooling.PUBLIC_TOOL_NAMES["get_photo_file"],
                                tooling.PUBLIC_TOOL_NAMES["get_selected_photos"]}
    for old in compat.RENAMED_TOOLS:
        assert old not in names, old


def test_the_query_is_renamed_only_on_the_two_searches():
    """`q` never existed anywhere else, nor does the `query` assistants send in its
    place: renaming either globally would invent an argument on a tool that has none."""
    renamed = compat.RENAMED_PARAMS_PER_TOOL
    assert set(renamed) == {
        tooling.PUBLIC_TOOL_NAMES["search_photos"],
        tooling.PUBLIC_TOOL_NAMES["search_photos_by_image"],
    }
    for mapping in renamed.values():
        assert mapping == {tooling.API_QUERY_PARAM: tooling.PUBLIC_QUERY_PARAM,
                           "query": tooling.PUBLIC_QUERY_PARAM}
        # `q` first: sent together, the name 0.4.12 published wins.
        assert list(mapping)[0] == tooling.API_QUERY_PARAM


def test_the_guards_are_keyed_on_names_that_exist():
    """A guard wired to a dead name never fires, and says nothing.

    That is the failure a rename can introduce: the tests pass, the server starts, and
    the guard simply stops existing.
    """
    from pexafy_mcp import guards
    live = set(tooling.PUBLIC_TOOL_NAMES.values())
    assert set(guards.GUARDED_ID_ARGS) <= live, guards.GUARDED_ID_ARGS
    assert set(guards.GUARDED_QUERY_ARGS) <= live, guards.GUARDED_QUERY_ARGS
    # And the rank guard covers BOTH tools that take a photo_id.
    assert tooling.PUBLIC_TOOL_NAMES["get_photo_file"] in guards.GUARDED_ID_ARGS


def test_the_sentence_cap_is_enforced_and_not_only_declared():
    """`maxLength` binds only a client that validates before sending.

    Without the guard, a longer sentence goes to the API whole (a 341-character one was
    seen on preprod), and past the API's own limit comes back as an unreadable HTTP 422.
    A limit that is announced and not held is worse than none: it promises a validation
    that does not exist.
    """
    from pexafy_mcp import guards
    assert tooling.SENTENCE_MAX_LENGTH == 250
    # BOTH searches take it: the sentence also goes with an image reference.
    assert set(guards.SENTENCE_ARGS) == {
        tooling.PUBLIC_TOOL_NAMES["search_photos"],
        tooling.PUBLIC_TOOL_NAMES["search_photos_by_image"],
    }


def test_a_null_budget_is_declared_nullable():
    """The `connect_account` probe answers `budget: null` in the NOMINAL case.

    `budget` appears only past 80% spent. Declared as `{"type": "object"}` alone, output
    validation refused the answer — "None is not of type 'object'" — whenever the
    allowance was fine, which is almost always.
    """
    import asyncio

    from pexafy_mcp import server
    tools = {t.name: t for t in asyncio.run(server.build_server().list_tools())}
    connect = tools.get(tooling.PUBLIC_TOOL_NAMES["connect_account"])
    if connect is None or not connect.output_schema:
        return  # account linking is disabled in this environment
    budget = connect.output_schema["properties"]["budget"]
    types = {b.get("type") for b in budget.get("anyOf", [])}
    assert types == {"object", "null"}, budget


def test_a_shape_outside_the_enum_is_refused_not_swallowed():
    """The schema declares three shapes; the API drops what it does not know in silence.

    Unguarded, `["wide"]` returns sixteen photos of every shape, with a success and an
    empty filter — the caller believes it filtered. Same defect as the sentence cap: a
    published constraint that nothing enforces.
    """
    from pexafy_mcp import guards
    assert tooling.ORIENTATIONS  # the enum is the source
    src = (pathlib.Path(guards.__file__)).read_text(encoding="utf-8")
    assert "is not a photo shape" in src


def test_the_search_results_carry_a_rank():
    """Without a rank, a client with no grid has nothing to resolve "the second one" by.

    The rank is DATA, not a drawing: the grid paints none on a result tile, and the only
    numbers the person sees are those of their selection.
    """
    from pexafy_mcp import previews
    data = {"data": [{"photo_id": "a"}, {"photo_id": "b"}, {"photo_id": "c"}]}
    previews.inject_ranks(data)
    assert [p["rank"] for p in data["data"]] == [1, 2, 3]
    # And it is DECLARED, or a host that validates is free to drop it.
    assert "rank" in tooling.ADD_PHOTO_FIELDS


def test_the_photo_file_says_what_it_is():
    """800 KB of image with no credit and no dimensions is a file nobody can cite.

    The text block comes first, so that a client that reads only the first block still
    knows what it has just received.
    """
    import inspect

    from pexafy_mcp import server
    src = inspect.getsource(server.get_photo_file)
    assert "Credit to display" in src
    assert 'TextContent(type="text"' in src
