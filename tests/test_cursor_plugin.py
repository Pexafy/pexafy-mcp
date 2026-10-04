"""The Cursor plugin in `cursor/`, held to what the Cursor Marketplace reads.

Cursor's publish form takes the repository's root URL only, so the root carries a
marketplace manifest, `.cursor-plugin/marketplace.json`, that points at the plugin
folder (cursor.com/docs/reference/plugins, "Cursor multi-plugin repositories"). The
plugin folder holds a Cursor manifest, the server declaration, the skill, a logo, a
README and the licence; Cursor's reviewers read it, and people who install the plugin
receive it.

The skill is the one in `skills/`, copied rather than linked, as for the Claude plugin
in `plugin/`: a copy has to be kept identical by hand, and this is where a forgotten
one fails.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlparse

import yaml

import pexafy_mcp

ROOT = Path(__file__).resolve().parent.parent
MARKETPLACE = ROOT / ".cursor-plugin" / "marketplace.json"
SKILL = Path("skills", "find-stock-photos", "SKILL.md")

# Everything the plugin folder holds, and so everything Cursor publishes.
FILES = {
    ".cursor-plugin/plugin.json",
    "mcp.json",
    "LICENSE",
    "README.md",
    "assets/logo.png",
    "skills/find-stock-photos/SKILL.md",
}

# Cursor's pattern for a plugin name (schemas/plugin.schema.json in cursor/plugins).
NAME = re.compile(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?")


def _marketplace() -> dict:
    return json.loads(MARKETPLACE.read_text(encoding="utf-8"))


def _folder() -> Path:
    (entry,) = _marketplace()["plugins"]
    return ROOT / entry["source"]


def _json(name: str) -> dict:
    return json.loads((_folder() / name).read_text(encoding="utf-8"))


def _registry() -> dict:
    return json.loads((ROOT / "server.json").read_text(encoding="utf-8"))


def _connector_url() -> str:
    (remote,) = _registry()["remotes"]
    return remote["url"]


def test_the_marketplace_lists_the_one_plugin():
    marketplace = _marketplace()
    assert marketplace["name"] == "pexafy"
    assert marketplace["owner"] == {"name": "Pexafy", "email": "support@pexafy.com"}
    (entry,) = marketplace["plugins"]
    assert entry["name"] == "pexafy"
    assert entry["source"] == "cursor"
    assert entry["description"].strip()


def test_the_folder_holds_only_what_is_published():
    found, links = set(), []
    for path in _folder().rglob("*"):
        if path.is_symlink():
            links.append(path.relative_to(_folder()).as_posix())
        elif path.is_file():
            found.add(path.relative_to(_folder()).as_posix())
    assert not links, f"copies, not symbolic links: {links}"
    assert found == FILES


def test_the_manifest_matches_the_marketplace_entry():
    manifest = _json(".cursor-plugin/plugin.json")
    (entry,) = _marketplace()["plugins"]
    assert manifest["name"] == entry["name"] == "pexafy"
    assert NAME.fullmatch(manifest["name"])
    assert manifest["displayName"] == "Pexafy"
    assert manifest["description"].strip()
    # Cursor's schema allows a name and an e-mail address in `author`, nothing else.
    assert manifest["author"] == {"name": "Pexafy", "email": "support@pexafy.com"}
    assert manifest["license"] == "MIT"
    assert manifest["repository"] == _registry()["repository"]["url"]
    assert urlparse(manifest["homepage"]).scheme == "https"
    assert (_folder() / manifest["logo"]).is_file()
    assert manifest["mcpServers"] == "./mcp.json"


def test_the_plugin_version_is_the_server_version():
    assert _json(".cursor-plugin/plugin.json")["version"] == pexafy_mcp.__version__


def test_the_server_is_the_hosted_connector():
    """The same URL as the Claude plugin and the registry entry, and no header: a
    header here would ship a credential to everyone who installs the plugin."""
    url = _connector_url()
    assert _json("mcp.json") == {"mcpServers": {"pexafy": {"type": "http", "url": url}}}


def test_the_skill_is_a_copy_of_the_repository_skill():
    copy = _folder() / SKILL
    assert not copy.is_symlink()
    assert copy.read_bytes() == (ROOT / SKILL).read_bytes(), (
        f"cursor/{SKILL.as_posix()} differs from {SKILL.as_posix()}: "
        f"cp {SKILL.as_posix()} cursor/{SKILL.as_posix()}")
    match = re.match(r"^---\n(.*?)\n---\n", copy.read_text(encoding="utf-8"), re.DOTALL)
    assert match, "SKILL.md has no frontmatter"
    front = yaml.safe_load(match.group(1))
    assert front["name"] == SKILL.parent.name
    assert front["description"].strip()


def test_the_readme_says_what_the_plugin_sends():
    readme = (_folder() / "README.md").read_text(encoding="utf-8")
    assert "https://pexafy.com/legal/privacy/" in readme
    assert _connector_url() in readme
    assert "## Data" in readme and "## Troubleshooting" in readme


def test_the_licence_is_the_repository_licence():
    assert (_folder() / "LICENSE").read_bytes() == (ROOT / "LICENSE").read_bytes()
