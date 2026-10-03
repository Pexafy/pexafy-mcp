"""What travels back to the frame so it can ask the same question again.

The grid's shape filter re-runs the call that produced the page on screen with a format
attached. It can only do that if it knows what the call was — and nothing in the
model's half of the answer says so. `_meta["pexafy/origin"]` is that channel; these
tests are about what may ride on it and what must not.
"""
from __future__ import annotations

from pexafy_mcp import origin, tooling


def test_a_text_search_travels_as_its_words():
    got = origin.origin_for(tooling.PUBLIC_TOOL_NAMES["search_photos"], {"english_search_sentence": "a dog on a beach"})
    assert got == {"tool": tooling.PUBLIC_TOOL_NAMES["search_photos"],
                   "arguments": {"english_search_sentence": "a dog on a beach"},
                   "orientation": []}


def test_the_shape_already_applied_comes_back_with_it():
    """The button opens showing what IS filtered, not what the frame last remembered
    pressing — a grid the assistant itself asked for portraits on has to open on
    "Portrait", or the reader is looking at a filter the widget denies applying."""
    got = origin.origin_for(tooling.PUBLIC_TOOL_NAMES["search_photos"], {"english_search_sentence": "cats",
                                              tooling.PUBLIC_ORIENTATION_PARAM: ["portrait"]})
    assert got["orientation"] == ["portrait"]


def test_the_api_name_for_the_shape_is_understood_too():
    """`origin_for` reads the shape under either name, so it does not depend on
    compat.py having translated an old caller's `orientation` first."""
    got = origin.origin_for(tooling.PUBLIC_TOOL_NAMES["search_photos"], {"english_search_sentence": "cats",
                                              tooling.API_ORIENTATION_PARAM: ["square"]})
    assert got["orientation"] == ["square"]


def test_a_single_shape_sent_as_a_string_is_still_a_list():
    """`null` vs `[]` vs a bare string is three ways to say one thing, and the frame
    tests the length of whatever it gets."""
    got = origin.origin_for(tooling.PUBLIC_TOOL_NAMES["search_photos_by_image"], {"photo_id": "abc", "orientation": "portrait"})
    assert got["orientation"] == ["portrait"]


def test_a_catalogue_photo_travels_as_its_reference_photo():
    got = origin.origin_for(tooling.PUBLIC_TOOL_NAMES["search_photos_by_image"], {"photo_id": "019e-abc"})
    assert got == {"tool": tooling.PUBLIC_TOOL_NAMES["search_photos_by_image"],
                   "arguments": {"photo_id": "019e-abc"},
                   "orientation": []}


def test_a_by_image_search_travels_as_a_link_and_only_as_a_link():
    """A link the reader pasted can be re-sent from the frame. An uploaded file cannot:
    it arrives as host-managed bytes, and echoing its identifier back would say what
    somebody uploaded to anyone who can read the frame's meta. No origin, no button —
    which is the honest outcome, because the call genuinely cannot be replayed."""
    assert origin.origin_for(tooling.PUBLIC_TOOL_NAMES["search_photos_by_image"],
                             {"image_url": "https://example.com/a.jpg"})["arguments"] == {
        "image_url": "https://example.com/a.jpg"}
    assert origin.origin_for(tooling.PUBLIC_TOOL_NAMES["search_photos_by_image"],
                             {"image_file": {"file_id": "f-1", "download_url": "https://x/y"}}) is None
    assert origin.origin_for(tooling.PUBLIC_TOOL_NAMES["search_photos_by_image"], {"image_base64": "AAAA"}) is None


def test_a_tool_that_is_not_a_search_carries_nothing():
    """The allow-list names what MAY travel, so a tool added later does not start
    leaking because nobody thought about it here."""
    for name in (tooling.PUBLIC_TOOL_NAMES["get_photo_file"], tooling.PUBLIC_TOOL_NAMES["get_selected_photos"], "connect_account", ""):
        assert origin.origin_for(name, {"photo_id": "abc"}) is None


def test_an_argument_nobody_allowed_is_dropped():
    """Per tool, and by name. `per_page` is not replayable from a frame — the page size
    is set server-side — and neither is anything a future release adds."""
    got = origin.origin_for(tooling.PUBLIC_TOOL_NAMES["search_photos"], {"english_search_sentence": "cats", "per_page": 99, "cursor": "abc"})
    assert got["arguments"] == {"english_search_sentence": "cats"}


def test_an_empty_argument_is_not_an_argument():
    """The frame builds its next call by spreading `arguments`, so an empty sentence
    would travel as an explicit empty search rather than as no search at all."""
    assert origin.origin_for(tooling.PUBLIC_TOOL_NAMES["search_photos"], {"english_search_sentence": ""}) is None
    assert origin.origin_for(tooling.PUBLIC_TOOL_NAMES["search_photos"], {"english_search_sentence": None}) is None
    assert origin.origin_for(tooling.PUBLIC_TOOL_NAMES["search_photos"], {}) is None


def test_the_key_is_namespaced_like_the_others():
    """Five things now ride on `_meta`, and a host merges them into one object."""
    from pexafy_mcp import budget, previews, selection
    keys = {origin.ORIGIN_META_KEY, origin.CTA_META_KEY, previews.PREVIEW_META_KEY,
            selection.SELECTION_META_KEY, budget.ACCOUNT_META_KEY}
    assert len(keys) == 5
    assert all(k.startswith("pexafy/") for k in keys)


# ── The link to the answer on the site, which rides on `_meta` too ────────────
# It is the grid's — the "Open in Pexafy" button, the wordmark, the site the frame
# adopts — and a model has no use for it: in `structuredContent` it was one more link
# handed to a reader who could only skip it.

def test_a_text_search_links_to_its_live_results():
    cta = origin.cta_for(tooling.PUBLIC_TOOL_NAMES["search_photos"], {"english_search_sentence": "two cats"})
    assert cta == {"label": "Open in Pexafy", "url": f"{origin.WEB_URL}/?q=two+cats"}


def test_a_catalogue_photo_links_to_its_page():
    """Its page lists the rest of its neighbours."""
    cta = origin.cta_for(tooling.PUBLIC_TOOL_NAMES["search_photos_by_image"],
                         {"photo_id": "abc-123", "english_search_sentence": "cats"})
    assert cta == {"label": "Open in Pexafy", "url": f"{origin.WEB_URL}/photos/abc-123/"}


def test_an_image_links_to_the_image_search_page():
    """No public URL mirrors an uploaded image, a link or bytes alike."""
    for arguments in ({"image_url": "https://example.com/a.jpg"}, {"image_base64": "AAAA"},
                      {"image_file": {"download_url": "https://x/y"}}):
        cta = origin.cta_for(tooling.PUBLIC_TOOL_NAMES["search_photos_by_image"], arguments)
        assert cta == {"label": "Image search at Pexafy", "url": origin.WEB_URL}, arguments


def test_a_tool_that_returns_no_grid_has_no_link():
    for name in (tooling.PUBLIC_TOOL_NAMES["get_photo_file"],
                 tooling.PUBLIC_TOOL_NAMES["get_selected_photos"], "connect_account", ""):
        assert origin.cta_for(name, {"photo_id": "abc"}) is None


def test_the_site_is_the_one_this_server_belongs_to(reload_with_env):
    """A preprod server sends its reader to preprod, never to production."""
    reloaded = reload_with_env(origin, {"PEXAFY_WEB_URL": "https://preprod.pexafy.com/"})
    cta = reloaded.cta_for(tooling.PUBLIC_TOOL_NAMES["search_photos"], {"english_search_sentence": "a dog"})
    assert cta["url"] == "https://preprod.pexafy.com/?q=a+dog"


def test_a_catalogue_photo_travels_with_the_words_it_was_found_under():
    """A refinement made from a text search carries that search's sentence; the shape
    filter on a refined page has to re-ask with the same words, or the filtered
    neighbours would drift off the subject the unfiltered ones were kept on."""
    got = origin.origin_for(tooling.PUBLIC_TOOL_NAMES["search_photos_by_image"], {"photo_id": "019e-abc", "english_search_sentence": "a red bicycle"})
    assert got["arguments"] == {"photo_id": "019e-abc", "english_search_sentence": "a red bicycle"}
    # …and without words, nothing is invented.
    assert origin.origin_for(tooling.PUBLIC_TOOL_NAMES["search_photos_by_image"], {"photo_id": "019e-abc", "english_search_sentence": ""})["arguments"] == {
        "photo_id": "019e-abc"}
