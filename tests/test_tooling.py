"""customize_spec tunes the kept tools and scrubs environment-specific bits."""
from __future__ import annotations

import copy

from pexafy_mcp import tooling


def _spec():
    """A minimal OpenAPI spec covering one kept tool plus a servers block."""
    return {
        "servers": [{"url": "https://internal.example.com"}],
        "paths": {
            "/api/v1/search/photos": {
                "get": {
                    "operationId": "search_photos_api_v1_search_photos_get",
                    "summary": "raw summary",
                    "parameters": [
                        {"name": "q", "in": "query", "description": "old", "schema": {}},
                        {"name": "color_name", "in": "query", "schema": {}},
                        {"name": "score_threshold", "in": "query", "schema": {}},
                        {"name": "sort_by", "in": "query", "schema": {}},
                    ],
                }
            }
        },
    }


FACETS = {"source": ["Unsplash", "Pexels"], "license_type": ["free", "cc0"]}


def test_drops_servers_block():
    spec = _spec()
    tooling.customize_spec(spec, FACETS)
    assert "servers" not in spec


def test_removes_misleading_params():
    spec = _spec()
    tooling.customize_spec(spec, FACETS)
    params = {p["name"] for p in spec["paths"]["/api/v1/search/photos"]["get"]["parameters"]}
    assert "score_threshold" not in params
    assert "sort_by" not in params
    assert {"q", "color_name"} <= params


def test_rewrites_description_and_drops_summary():
    spec = _spec()
    tooling.customize_spec(spec, FACETS)
    op = spec["paths"]["/api/v1/search/photos"]["get"]
    assert "summary" not in op
    assert "semantic" in op["description"].lower()
    # The shape of a result is appended to every search tool, as description rather
    # than instruction: Anthropic's connector review rejects a tool description that
    # tells the assistant how to behave.
    assert "Each result carries" in op["description"]
    for imperative in ("ALWAYS", "Do NOT", "proactively", "you must show"):
        assert imperative not in op["description"], imperative


def test_hardcodes_value_sets_into_param_docs():
    spec = _spec()
    tooling.customize_spec(spec, FACETS)
    by_name = {p["name"]: p for p in spec["paths"]["/api/v1/search/photos"]["get"]["parameters"]}
    # Colors come from the static constant; sources come from facets.
    assert "blue" in by_name["color_name"]["description"]
    # q gets an example mirrored into the schema too.
    assert by_name["q"].get("example")
    assert by_name["q"]["schema"].get("example")


def test_is_idempotent():
    spec = _spec()
    tooling.customize_spec(spec, FACETS)
    once = copy.deepcopy(spec)
    tooling.customize_spec(spec, FACETS)
    assert spec == once
