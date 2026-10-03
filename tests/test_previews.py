"""Signed thumbnail URLs and preview injection."""
from __future__ import annotations

import re
import time

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


def test_inject_preview_urls_adds_a_larger_preview_for_the_viewer(monkeypatch):
    """The viewer fills the surface with one photo and needs a sharper file than the
    16-up grid does. It is signed for its OWN width: the HMAC covers the width, so a
    widget that rewrote 480w to 1280w in the URL it was handed would get a 403."""
    monkeypatch.setattr(previews, "PREVIEWS_AVAILABLE", True)
    monkeypatch.setattr(previews, "THUMB_BASE_URL", "https://thumb.test")
    monkeypatch.setattr(previews, "THUMB_HMAC_SECRET", "s3cr3t")

    data = {"data": [{"photo_id": "p1"}]}
    previews.inject_preview_urls(data)
    photo = data["data"][0]
    assert f"/{previews.THUMB_WIDTH}w.jpg" in photo["preview_url"]
    assert f"/{previews.PERMANENT_WIDTH}w.jpg" in photo["preview_url_large"]
    assert previews.PERMANENT_WIDTH > previews.THUMB_WIDTH


def test_the_viewers_preview_never_expires(monkeypatch):
    """A tool result stays in the thread, and past an expiry the proxy answers 410
    ("Preview unavailable"). The viewer's file uses the proxy's no-expiry form — no
    `?e` at all, and it is only accepted at PERMANENT_WIDTH, which is why the width is
    not a parameter of that signer."""
    monkeypatch.setattr(previews, "PREVIEWS_AVAILABLE", True)
    monkeypatch.setattr(previews, "THUMB_BASE_URL", "https://thumb.test")
    monkeypatch.setattr(previews, "THUMB_HMAC_SECRET", "s3cr3t")

    url = previews.sign_thumb_url_permanent("p1")
    assert re.fullmatch(r"https://thumb\.test/p1/1280w\.jpg\?s=[0-9a-f]{16}", url)
    assert "e=" not in url
    # Same photo, same URL, forever — that is what "permanent" buys.
    assert url == previews.sign_thumb_url_permanent("p1")


def test_the_grid_url_outlives_the_conversation_and_is_stable(monkeypatch):
    """The grid cannot use the permanent form (that is 1280w only, seven times the
    bytes for a 140px tile), so it takes a long expiry instead — and rounds it up to a
    fixed step, so two searches on the same day hand out the SAME URL rather than two
    that differ only in their token."""
    monkeypatch.setattr(previews, "THUMB_BASE_URL", "https://thumb.test")
    monkeypatch.setattr(previews, "THUMB_HMAC_SECRET", "s3cr3t")

    a = previews.sign_thumb_url("p1")
    b = previews.sign_thumb_url("p1")
    assert a == b

    exp = int(a.split("e=")[1].split("&")[0])
    assert exp - time.time() > 20 * 24 * 3600, "a grid thumbnail must outlive the thread"
    assert exp % previews.THUMB_TTL_STEP == 0


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
