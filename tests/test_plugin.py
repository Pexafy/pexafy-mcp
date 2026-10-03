"""The Claude plugin in `plugin/`, held to what Anthropic's directory checks.

The directory reads the plugin folder when it is submitted, then again on every new
commit of the branch it follows (claude.com/docs/plugins/pre-submission-checklist).
What it blocks on is mechanical — a manifest field, a README under 40 words, a server
URL that is not https, a symbolic link where the plugin loads a file, a skill whose
frontmatter does not parse — and each one costs a round trip through the portal to
find out. `claude plugin validate --strict plugin` stays the authoritative check of the
manifest schema; these tests keep the rest from drifting.

The skill is the one in `skills/`, copied rather than linked, because the directory
refuses a symbolic link: a copy has to be kept identical by hand, and this is where a
forgotten one fails.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlparse

import yaml

import pexafy_mcp

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugin"
SKILL = Path("skills", "find-stock-photos", "SKILL.md")

# Everything the plugin folder holds. People who install the plugin receive exactly
# this folder, so whatever lands here is published, scanned and shipped to them — the
# skill's `agents/openai.yaml` and icons are ChatGPT's and stay out.
FILES = {
    ".claude-plugin/plugin.json",
    ".mcp.json",
    "LICENSE",
    "README.md",
    "skills/find-stock-photos/SKILL.md",
}

# The directory's pattern for a plugin name, which is also the one for a skill name.
NAME = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?")


def _json(name: str) -> dict:
    return json.loads((PLUGIN / name).read_text(encoding="utf-8"))


def _registry() -> dict:
    return json.loads((ROOT / "server.json").read_text(encoding="utf-8"))


def _connector_url() -> str:
    """The hosted server's URL, as the MCP registry publishes it."""
    (remote,) = _registry()["remotes"]
    return remote["url"]


def _sections(markdown: str) -> dict[str, str]:
    parts = re.split(r"^## +(.+?)\s*$", markdown, flags=re.M)
    return {title: body for title, body in zip(parts[1::2], parts[2::2])}


def test_the_folder_holds_only_what_is_published():
    found, links = set(), []
    for path in PLUGIN.rglob("*"):
        if path.is_symlink():
            links.append(path.relative_to(PLUGIN).as_posix())
        elif path.is_file():
            found.add(path.relative_to(PLUGIN).as_posix())
    assert not links, f"symbolic links are refused where the plugin loads them: {links}"
    assert found == FILES


def test_every_file_is_small_text():
    """A file over 256 KiB, or one that is not text, holds the version for a reviewer."""
    for name in sorted(FILES):
        data = (PLUGIN / name).read_bytes()
        assert len(data) < 256 * 1024, name
        data.decode("utf-8")


def test_the_manifest_carries_what_the_directory_reads():
    manifest = _json(".claude-plugin/plugin.json")
    # Only metadata: a component key here would change what loads, and an unknown key
    # is stripped at load time with a warning.
    assert set(manifest) == {"name", "displayName", "version", "description", "author",
                             "homepage", "repository", "license", "keywords"}
    # Permanent once listed: people install the plugin and refer to it by this name.
    assert manifest["name"] == "pexafy"
    assert NAME.fullmatch(manifest["name"])
    assert manifest["displayName"] == "Pexafy"
    assert manifest["description"].strip()
    assert manifest["author"] == {
        "name": "Pexafy", "email": "support@pexafy.com", "url": "https://pexafy.com"}
    assert manifest["license"] == "MIT"
    assert manifest["repository"] == _registry()["repository"]["url"]
    homepage = urlparse(manifest["homepage"])
    assert homepage.scheme == "https" and homepage.netloc, manifest["homepage"]
    assert manifest["keywords"] and all(isinstance(k, str) for k in manifest["keywords"])


def test_the_plugin_version_is_the_server_version():
    """A plugin that sets `version` keeps its users on it until it changes, so a new
    skill shipped under an old number would never reach them. The skill is released
    with the server, and the plugin takes the server's number."""
    assert _json(".claude-plugin/plugin.json")["version"] == pexafy_mcp.__version__


def test_the_server_is_the_hosted_connector():
    """The same URL as the connector submitted to the directory, so that someone who has
    both sees one set of tools rather than two — and nothing else: a header here would
    ship a credential to everyone who installs the plugin."""
    url = _connector_url()
    assert urlparse(url).scheme == "https"
    assert _json(".mcp.json") == {"mcpServers": {"pexafy": {"type": "http", "url": url}}}


def test_the_skill_is_a_copy_of_the_repository_skill():
    copy = PLUGIN / SKILL
    assert not copy.is_symlink()
    assert copy.read_bytes() == (ROOT / SKILL).read_bytes(), (
        f"plugin/{SKILL.as_posix()} differs from {SKILL.as_posix()}: "
        f"cp {SKILL.as_posix()} plugin/{SKILL.as_posix()}")


def test_the_skill_frontmatter_is_one_claude_loads():
    text = (PLUGIN / SKILL).read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    assert match, "SKILL.md has no frontmatter"
    front = yaml.safe_load(match.group(1))
    assert front["name"] == SKILL.parent.name
    assert NAME.fullmatch(front["name"])
    assert isinstance(front["description"], str)
    assert 0 < len(front["description"].strip()) <= 1024


def test_the_readme_says_what_the_plugin_sends():
    readme = (PLUGIN / "README.md").read_text(encoding="utf-8")
    # The directory does not count words inside code blocks.
    prose = re.sub(r"^```.*?^```", "", readme, flags=re.M | re.S)
    assert len(re.findall(r"\w[\w'’-]*", prose)) >= 40
    sections = _sections(readme)
    # Everything the plugin sends or fetches, and the policy that covers it.
    assert "https://pexafy.com/legal/privacy/" in sections["Data"]
    assert _connector_url() in readme
    examples = re.findall(r"^- \S", sections["Examples"], flags=re.M)
    assert len(examples) >= 3
    assert sections["Troubleshooting"].strip()


def test_the_licence_is_the_repository_licence():
    assert (PLUGIN / "LICENSE").read_bytes() == (ROOT / "LICENSE").read_bytes()
