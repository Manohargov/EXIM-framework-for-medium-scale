"""Web photo lookup: relevance filtering, downloads, cache and offline escape.

Nothing here touches the network. Providers and ``urlopen`` are replaced with
tiny fakes, so the suite stays fast and deterministic while still exercising the
paths that matter: ranking, junk rejection, fallback and the disk cache.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from ecomops import webimages
from ecomops.webimages import (
    FetchedImage,
    ImageHit,
    WebImageError,
    describe,
    download_image,
    fetch_item_image,
    search_item_images,
    search_queries,
    usable,
)


def _png(width: int = 640, height: int = 640) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (200, 120, 60)).save(buffer, "PNG")
    return buffer.getvalue()


class _Response(io.BytesIO):
    def __init__(self, body: bytes, content_type: str = "image/png") -> None:
        super().__init__(body)
        self.headers = {"Content-Type": content_type}

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


@pytest.fixture(autouse=True)
def online(monkeypatch):
    """Lookups are enabled and the cache is a temp dir unless a test says otherwise."""
    monkeypatch.setenv("ECOMOPS_WEB_IMAGES", "on")
    monkeypatch.setattr(webimages, "CACHE_DIR", Path("/nonexistent-cache"))


# ------------------------------------------------------------------- relevance

def test_relevance_counts_matching_title_words():
    hit = ImageHit("Bottle of Extra Virgin Olive Oil.jpg", "http://x/1.png", "wikimedia")
    assert webimages.relevance(hit, "olive oil bottle") == pytest.approx(1.0)
    assert webimages.relevance(hit, "olive oil lamp") == pytest.approx(2 / 3)


def test_a_map_that_mentions_the_words_is_not_a_product_photo():
    junk = ImageHit("Map of the wreck finds of flight MH370.png", "http://x/2.png", "wikimedia")
    assert webimages.relevance(junk, "aluminium laptop stand") == 0.0
    assert not usable(junk, "aluminium laptop stand", min_score=webimages.MIN_SCORE)

    good = ImageHit("Aluminium laptop stand.jpg", "http://x/3.png", "wikimedia")
    assert usable(good, "aluminium laptop stand", min_score=webimages.MIN_SCORE)


def test_diagram_titles_are_blocked_even_when_relevant():
    hit = ImageHit("Olive oil bottle diagram.svg", "http://x/4.png", "openverse")
    assert not usable(hit, "olive oil bottle", min_score=0.0)


def test_search_ranks_hits_and_drops_off_topic_ones(monkeypatch):
    hits = [
        ImageHit("Some beach holiday.jpg", "http://x/a.jpg", "wikimedia"),
        ImageHit("Wireless earbuds in case.jpg", "http://x/b.jpg", "wikimedia"),
        ImageHit("Chart of earbud sales.png", "http://x/c.png", "wikimedia"),
    ]
    calls = []

    def fake_search(query, *, limit, timeout):
        calls.append(query)
        return hits

    found = search_item_images(
        "wireless earbuds", providers=(("fake", fake_search),),
    )
    assert [hit.url for hit in found] == ["http://x/b.jpg"]
    assert calls == ["wireless earbuds"]


def test_second_provider_is_used_when_the_first_has_only_junk():
    def empty(query, *, limit, timeout):
        return [ImageHit("Map.png", "http://x/map.png", "one")]

    def good(query, *, limit, timeout):
        return [ImageHit("Ceramic mug.jpg", "http://x/mug.jpg", "two")]

    found = search_item_images(
        "ceramic mug", providers=(("empty", empty), ("good", good)),
    )
    assert [hit.provider for hit in found] == ["two"]


def test_search_queries_drop_sizes_and_end_with_two_words():
    assert search_queries("Insulated water bottle 750ml") == [
        "Insulated water bottle 750ml", "Insulated water bottle", "water bottle",
    ]
    assert [query.lower() for query in search_queries("HDMI cable 2m")][-1] == "hdmi cable"
    assert search_queries("Backpack 25l") == ["Backpack 25l", "Backpack"]
    assert search_queries("   ") == []


def test_fetch_retries_a_simpler_query_when_the_full_name_finds_nothing(tmp_path: Path, monkeypatch):
    asked = []

    def fake_search(query, *, limit, timeout):
        asked.append(query)
        if query.lower() != "water bottle":
            return []
        return [ImageHit("Water bottle.jpg", "http://x/bottle.png", "wikimedia")]

    monkeypatch.setattr(
        webimages.urllib.request, "urlopen", lambda *a, **k: _Response(_png()),
    )

    photo = fetch_item_image(
        "Insulated water bottle 750ml", folder=tmp_path, providers=(("fake", fake_search),),
    )
    assert photo is not None and photo.title == "Water bottle.jpg"
    assert asked == ["Insulated water bottle 750ml", "Insulated water bottle", "water bottle"]
    # The poster only ever sees the catalogue name.
    assert photo.name == "Insulated water bottle 750ml"


def test_search_is_off_when_the_feature_flag_is_off(monkeypatch):
    monkeypatch.setenv("ECOMOPS_WEB_IMAGES", "off")

    def boom(query, *, limit, timeout):  # pragma: no cover - must not be called
        raise AssertionError("providers must not be consulted while disabled")

    assert search_item_images("olive oil", providers=(("boom", boom),)) == []
    assert fetch_item_image("olive oil") is None


# ------------------------------------------------------------------- downloads

def test_download_returns_the_bytes_for_a_real_photo(monkeypatch):
    monkeypatch.setattr(
        webimages.urllib.request, "urlopen", lambda *a, **k: _Response(_png()),
    )
    assert download_image("http://x/ok.png").startswith(b"\x89PNG")


@pytest.mark.parametrize(
    "response",
    [
        _Response(b"<html>nope</html>", "text/html"),
        _Response(b"\x89PNG\r\n\x1a\n", "image/png"),          # too small to decode
        _Response(b"", "image/png"),
    ],
)
def test_download_rejects_payloads_that_are_not_usable_photos(monkeypatch, response):
    monkeypatch.setattr(webimages.urllib.request, "urlopen", lambda *a, **k: response)
    with pytest.raises(WebImageError):
        download_image("http://x/bad.png")


def test_download_refuses_an_index_page(monkeypatch):
    def boom(*args, **kwargs):  # pragma: no cover - must not be called
        raise AssertionError("index pages must not be fetched")

    monkeypatch.setattr(webimages.urllib.request, "urlopen", boom)
    with pytest.raises(WebImageError):
        download_image("https://duckduckgo.com/i.js?q=mug")


# ----------------------------------------------------------------------- cache

def test_fetch_writes_and_reuses_the_cache(tmp_path: Path, monkeypatch):
    downloads = []

    def fake_search(query, *, limit, timeout):
        return [ImageHit("Olive oil bottle.jpg", "http://x/olive.png", "wikimedia", license="CC0")]

    def fake_open(*args, **kwargs):
        downloads.append(args)
        return _Response(_png())

    monkeypatch.setattr(webimages.urllib.request, "urlopen", fake_open)

    first = fetch_item_image("olive oil bottle", folder=tmp_path, providers=(("fake", fake_search),))
    assert first is not None and not first.cached
    assert first.provider == "wikimedia" and first.license == "CC0"
    assert list(tmp_path.glob("*.img"))

    second = fetch_item_image("olive oil bottle", folder=tmp_path, providers=(("fake", fake_search),))
    assert second is not None and second.cached
    assert len(downloads) == 1


def test_fetch_returns_none_when_nothing_relevant_is_found(tmp_path: Path):
    def junk(query, *, limit, timeout):
        return [ImageHit("Map of something.png", "http://x/map.png", "fake")]

    assert fetch_item_image("laptop stand", folder=tmp_path, providers=(("junk", junk),)) is None


def test_describe_summarises_provenance():
    assert describe(None) == "no photo found"
    fetched = FetchedImage(image=b"x", name="mug", provider="wikimedia", license="CC0", cached=True)
    assert describe(fetched) == "web:wikimedia · CC0 · cached"
