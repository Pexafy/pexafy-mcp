"""Signed thumbnail URLs and preview injection."""
from __future__ import annotations

import re

from pexafy_mcp import previews


def test_sign_thumb_url_shape(monkeypatch):
    monkeypatch.setattr(previews, "THUMB_BASE_URL", "https://thumb.test")
    monkeypatch.setattr(previews, "THUMB_HMAC_SECRET", "s3cr3t")
    monkeypatch.setattr(previews, "THUMB_WIDTH", 480)

    url = previews.sign_thumb_url("abc123")
    assert re.fullmatch(r"https://thumb\.test/abc123/480w\.jpg\?e=\d+&s=[0-9a-f]{16}", url)


def test_sign_thumb_url_secret_changes_signature(monkeypatch):
    monkeypatch.setattr(previews, "THUMB_BASE_URL", "https://thumb.test")
    monkeypatch.setattr(previews, "THUMB_WIDTH", 480)

    monkeypatch.setattr(previews, "THUMB_HMAC_SECRET", "one")
    monkeypatch.setattr(previews, "THUMB_TTL", 3600)
    a = previews.sign_thumb_url("pid")
    monkeypatch.setattr(previews, "THUMB_HMAC_SECRET", "two")
    b = previews.sign_thumb_url("pid")
    # Different secret → different signature segment.
    assert a.split("&s=")[1] != b.split("&s=")[1]


def test_inject_preview_urls_noop_when_unavailable(monkeypatch):
    monkeypatch.setattr(previews, "PREVIEWS_AVAILABLE", False)
    data = {"data": [{"photo_id": "x"}]}
    previews.inject_preview_urls(data)
    assert "preview_url" not in data["data"][0]


def test_inject_preview_urls_adds_signed_url(monkeypatch):
    monkeypatch.setattr(previews, "PREVIEWS_AVAILABLE", True)
    monkeypatch.setattr(previews, "THUMB_BASE_URL", "https://thumb.test")
    monkeypatch.setattr(previews, "THUMB_HMAC_SECRET", "s3cr3t")

    data = {"data": [{"photo_id": "p1"}, {"photo_id": "p2"}, {"no_id": True}]}
    previews.inject_preview_urls(data)
    assert data["data"][0]["preview_url"].startswith("https://thumb.test/p1/")
    assert data["data"][1]["preview_url"].startswith("https://thumb.test/p2/")
    assert "preview_url" not in data["data"][2]


def test_inject_preview_urls_accepts_bare_list(monkeypatch):
    monkeypatch.setattr(previews, "PREVIEWS_AVAILABLE", True)
    monkeypatch.setattr(previews, "THUMB_BASE_URL", "https://thumb.test")
    monkeypatch.setattr(previews, "THUMB_HMAC_SECRET", "s3cr3t")

    photos = [{"photo_id": "p1"}]
    previews.inject_preview_urls(photos)
    assert "preview_url" in photos[0]


def test_inject_preview_urls_does_not_overwrite(monkeypatch):
    monkeypatch.setattr(previews, "PREVIEWS_AVAILABLE", True)
    monkeypatch.setattr(previews, "THUMB_BASE_URL", "https://thumb.test")
    monkeypatch.setattr(previews, "THUMB_HMAC_SECRET", "s3cr3t")

    data = {"data": [{"photo_id": "p1", "preview_url": "keep-me"}]}
    previews.inject_preview_urls(data)
    assert data["data"][0]["preview_url"] == "keep-me"


def test_inject_ranks_numbers_results():
    data = {"data": [{"photo_id": "a"}, {"photo_id": "b"}, {"photo_id": "c"}]}
    previews.inject_ranks(data)
    assert [p["rank"] for p in data["data"]] == [1, 2, 3]


def test_inject_ranks_runs_without_previews(monkeypatch):
    # Ranks are independent of the thumbnail CDN config.
    monkeypatch.setattr(previews, "PREVIEWS_AVAILABLE", False)
    photos = [{"photo_id": "a"}, {"photo_id": "b"}]
    previews.inject_ranks(photos)
    assert [p["rank"] for p in photos] == [1, 2]


def test_inject_ranks_does_not_overwrite():
    data = {"data": [{"photo_id": "a", "rank": 99}]}
    previews.inject_ranks(data)
    assert data["data"][0]["rank"] == 99
