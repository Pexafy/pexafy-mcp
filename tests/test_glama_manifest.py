"""The maintainer claim has to be readable where Glama actually looks for it.

Glama's connector listing reported `glama.json not found (HTTP 404)` while the
file was sitting on the default branch of the public repository, publicly
readable. It was not looking at the repository: it fetches
`/.well-known/glama.json` on the host that serves the MCP server, unauthenticated,
every half hour or so. So the same claim is served from the server too — and the
two copies must say the same thing, or claiming the listing breaks the next time
someone edits one of them.
"""
import json
from pathlib import Path

import pytest

PATH = "/.well-known/glama.json"
REPO_FILE = Path(__file__).resolve().parents[1] / "glama.json"


@pytest.fixture
def app():
    from pexafy_mcp import server

    return server.build_server().http_app()


def _get(app, path):
    from starlette.testclient import TestClient

    with TestClient(app) as client:
        return client.get(path)


def test_manifest_is_served_without_credentials(app):
    response = _get(app, PATH)
    assert response.status_code == 200
    assert response.json()["maintainers"]


def test_manifest_matches_the_repository_file():
    """The claim Glama reads over HTTP is the claim in the repository."""
    from pexafy_mcp.server import GLAMA_MANIFEST

    assert json.loads(REPO_FILE.read_text()) == GLAMA_MANIFEST


def test_maintainers_are_github_usernames():
    """Their schema takes GitHub usernames — an email or a URL silently voids the claim."""
    from pexafy_mcp.server import GLAMA_MANIFEST

    for name in GLAMA_MANIFEST["maintainers"]:
        assert name and "@" not in name and "/" not in name
