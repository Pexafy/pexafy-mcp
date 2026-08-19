"""The version is written in three files, and they must agree.

`pyproject.toml` builds the package, `__init__.py` is what the running server
reports, and `server.json` is what the MCP registry publishes. Nothing ties them
together, and 0.4.2 shipped with `__init__.py` still saying 0.4.1 — the deploy
script prints that string as "version on disk", so the one place a human looks to
confirm what production is running was the one place that was wrong.
"""
from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import pexafy_mcp

ROOT = Path(__file__).resolve().parent.parent


def test_pyproject_and_package_agree():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert pexafy_mcp.__version__ == pyproject["project"]["version"]


def test_the_registry_manifest_agrees():
    manifest = json.loads((ROOT / "server.json").read_text())
    assert manifest["version"] == pexafy_mcp.__version__


def test_the_changelog_documents_this_version():
    """A release with no entry is a release nobody can read afterwards."""
    changelog = (ROOT / "CHANGELOG.md").read_text()
    assert re.search(rf"^## \[{re.escape(pexafy_mcp.__version__)}\]", changelog, re.M)
