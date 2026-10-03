"""The one reading of an on/off setting, shared by every module that has a switch."""
from __future__ import annotations

import pytest

from pexafy_mcp import compat, previews, selection
from pexafy_mcp.env import flag

NAME = "PEXAFY_TEST_SWITCH"


@pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes", " on "])
def test_the_words_for_on(monkeypatch, raw):
    monkeypatch.setenv(NAME, raw)
    assert flag(NAME, False) is True


@pytest.mark.parametrize("raw", ["0", "false", "no", "off", "enabled"])
def test_anything_else_is_off_even_when_the_default_is_on(monkeypatch, raw):
    monkeypatch.setenv(NAME, raw)
    assert flag(NAME, True) is False


@pytest.mark.parametrize("default", [True, False])
def test_unset_or_empty_is_the_default(monkeypatch, default):
    monkeypatch.delenv(NAME, raising=False)
    assert flag(NAME, default) is default
    monkeypatch.setenv(NAME, "")
    assert flag(NAME, default) is default


@pytest.mark.parametrize(("raw", "expected"), [("true", True), ("on", True), ("0", False)])
def test_previews_read_the_same_words(reload_with_env, raw, expected):
    """PEXAFY_MCP_PREVIEWS used to accept "1" alone: "true" switched the grid off."""
    reloaded = reload_with_env(previews, {
        "PEXAFY_MCP_PREVIEWS": raw,
        "PEXAFY_THUMB_BASE_URL": "https://thumb.test",
        "PEXAFY_THUMB_HMAC_SECRET": "s3cr3t",
    })
    assert reloaded.PREVIEWS_AVAILABLE is expected


@pytest.mark.parametrize(("raw", "expected"), [("", True), ("yes", True), ("off", False)])
def test_the_selection_tool_reads_the_same_words(reload_with_env, raw, expected):
    assert reload_with_env(selection, {"PEXAFY_SELECTION_TOOL": raw}).TOOL_ENABLED is expected


@pytest.mark.parametrize(("raw", "expected"), [("", True), ("yes", True), ("off", False), ("0", False)])
def test_the_similar_alias_reads_the_same_words(reload_with_env, raw, expected):
    assert reload_with_env(compat, {"PEXAFY_SIMILAR_ALIAS": raw}).SIMILAR_ALIAS is expected
