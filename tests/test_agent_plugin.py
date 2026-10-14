"""The plugin in `agent-plugin/`, in the Agent Plugins 1.0.0 format (agent-plugins.org).

GitHub Copilot's public plugin index (github/awesome-copilot) only accepts a plugin
whose `plugin.json` sits at the plugin root, in this format; Copilot, Codex and Qwen
Code read it as it is. The folder holds the manifest, the server declaration, the
skill, a README and the licence, and nothing that runs on the user's computer.

The skill is the one in `skills/`, copied rather than linked, as for `plugin/` and
`cursor/`: a copy has to be kept identical by hand, and this is where a forgotten one
fails. The version is the server's, like the other two plugins'.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlparse

import yaml

import pexafy_mcp

ROOT = Path(__file__).resolve().parent.parent
FOLDER = ROOT / "agent-plugin"
SKILL = Path("skills", "find-stock-photos", "SKILL.md")

# Everything the folder holds, and so everything an agent installs.
FILES = {
    "plugin.json",
    "mcp.json",
    "LICENSE",
    "README.md",
    "skills/find-stock-photos/SKILL.md",
}

# plugin.schema.json 1.0.0: the only properties it allows (additionalProperties: false),
# and its pattern for a name.
ALLOWED = {"$schema", "name", "version", "description", "author", "homepage", "repository",
           "license", "keywords", "extensions"}
NAME = re.compile(r"^(?!.*(?:--|\.\.))[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$")
# awesome-copilot's marketplace validation: keywords are lowercase hyphenated tags.
KEYWORD = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _json(name: str) -> dict:
    return json.loads((FOLDER / name).read_text(encoding="utf-8"))


def _registry() -> dict:
    return json.loads((ROOT / "server.json").read_text(encoding="utf-8"))


def _connector_url() -> str:
    (remote,) = _registry()["remotes"]
    return remote["url"]


def test_the_folder_holds_only_what_is_published():
    found, links = set(), []
    for path in FOLDER.rglob("*"):
        if path.is_symlink():
            links.append(path.relative_to(FOLDER).as_posix())
        elif path.is_file():
            found.add(path.relative_to(FOLDER).as_posix())
    assert not links, f"copies, not symbolic links: {links}"
    assert found == FILES


def test_the_manifest_is_an_agent_plugins_manifest():
    manifest = _json("plugin.json")
    assert manifest["$schema"] == "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
    assert set(manifest) <= ALLOWED, set(manifest) - ALLOWED
    assert manifest["name"] == "pexafy"
    assert NAME.fullmatch(manifest["name"])
    assert manifest["description"].strip()
    assert manifest["author"] == {"name": "Pexafy", "email": "support@pexafy.com",
                                  "url": "https://pexafy.com"}
    assert manifest["license"] == "MIT"
    assert manifest["repository"] == _registry()["repository"]["url"]
    assert urlparse(manifest["homepage"]).scheme == "https"
    assert manifest["keywords"] and all(KEYWORD.fullmatch(k) for k in manifest["keywords"])


def test_the_plugin_version_is_the_server_version():
    assert _json("plugin.json")["version"] == pexafy_mcp.__version__


def test_the_server_is_the_hosted_connector():
    """The same URL as the other plugins and the registry entry, and no header: a header
    here would ship a credential to everyone who installs the plugin."""
    assert _json("mcp.json") == {
        "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
        "mcpServers": {"pexafy": {"type": "streamable-http", "url": _connector_url()}},
    }


def test_the_skill_is_a_copy_of_the_repository_skill():
    copy = FOLDER / SKILL
    assert not copy.is_symlink()
    assert copy.read_bytes() == (ROOT / SKILL).read_bytes(), (
        f"agent-plugin/{SKILL.as_posix()} differs from {SKILL.as_posix()}: "
        f"cp {SKILL.as_posix()} agent-plugin/{SKILL.as_posix()}")
    match = re.match(r"^---\n(.*?)\n---\n", copy.read_text(encoding="utf-8"), re.DOTALL)
    assert match, "SKILL.md has no frontmatter"
    front = yaml.safe_load(match.group(1))
    assert front["name"] == SKILL.parent.name
    assert front["description"].strip()


def test_the_readme_says_what_the_plugin_sends():
    readme = (FOLDER / "README.md").read_text(encoding="utf-8")
    assert "https://pexafy.com/legal/privacy/" in readme
    assert _connector_url() in readme
    assert "## Data" in readme and "## Troubleshooting" in readme


def test_the_licence_is_the_repository_licence():
    assert (FOLDER / "LICENSE").read_bytes() == (ROOT / "LICENSE").read_bytes()
