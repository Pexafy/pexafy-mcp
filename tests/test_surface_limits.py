"""Limits a host puts on what it reads of this server, pinned so a text edit cannot
silently cross them."""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from pexafy_mcp import previews, server, tooling

# Claude Code truncates the server instructions and each tool description at 2,048
# characters (code.claude.com/docs/en/mcp); past it, the tail never reaches the model.
CLAUDE_CODE_LIMIT = 2048

SKILL = Path(__file__).resolve().parent.parent / "skills" / "find-stock-photos" / "SKILL.md"


@pytest.fixture
def production_server(monkeypatch):
    """The five tools production serves: the file tool exists only with previews on."""
    monkeypatch.setattr(previews, "PREVIEWS_AVAILABLE", True)
    return server.build_server()


async def test_the_instructions_fit_before_claude_code_cuts(production_server):
    assert len(production_server.instructions) < CLAUDE_CODE_LIMIT


async def test_every_tool_description_fits_before_claude_code_cuts(production_server):
    tools = await production_server.list_tools()
    assert len(tools) == 5, [t.name for t in tools]
    for tool in tools:
        assert len(tool.description or "") < CLAUDE_CODE_LIMIT, (tool.name, len(tool.description))


async def test_only_the_text_search_is_loaded_up_front(production_server):
    """Claude Code loads a deferred tool's description only once it searches for it;
    `anthropic/alwaysLoad` exempts the entry point, and only the entry point."""
    loaded = {
        tool.name for tool in await production_server.list_tools()
        if (tool.to_mcp_tool().meta or {}).get("anthropic/alwaysLoad") is True
    }
    assert loaded == {tooling.PUBLIC_TOOL_NAMES["search_photos"]}


def test_the_skill_frontmatter_passes_the_skill_validator_rules():
    """The rules of skill-creator's quick_validate.py. An unquoted `: ` inside the
    description made the whole frontmatter invalid YAML once already."""
    match = re.match(r"^---\n(.*?)\n---", SKILL.read_text(encoding="utf-8"), re.DOTALL)
    assert match, "SKILL.md has no frontmatter"
    front = yaml.safe_load(match.group(1))
    assert isinstance(front, dict)
    assert set(front) <= {"name", "description", "license", "allowed-tools", "metadata", "compatibility"}
    name = front["name"]
    assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name) and len(name) <= 64, name
    description = front["description"].strip()
    assert 0 < len(description) <= 1024, len(description)
    assert "<" not in description and ">" not in description
