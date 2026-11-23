"""Item photos from the open web, for the advertisement poster.

Given a product name the module searches an open image index, downloads the first
usable photo and normalises proof of where it came from (provider, title,
licence). Two keyless providers are tried in order:

* Wikimedia Commons — curated, licence-clear, and generous with direct URLs;
* Openverse — a broader CC index, used only when Commons comes up empty.

Everything degrades to ``None``: no network, no API key, a blocked host or an
unreadable payload all mean "render the monogram card instead", never an
exception on the poster path. Downloads are cached on disk by query, so a demo
and the test suite stay fast and deterministic after the first fetch.

The module is deliberately independent of ``advert`` (the renderer must not
depend on the network); ``ECOMOPS_WEB_IMAGES=off`` disables lookups entirely.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

log = logging.getLogger(__name__)

USER_AGENT = "ecomops/0.4 (clearance poster; demo)"
MAX_BYTES = 8 * 1024 * 1024      # refuse anything larger than 8 MB
MIN_EDGE = 200                   # ignore thumbnails too small to print
TIMEOUT = 8.0

_REPO_ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = Path(os.environ.get("ECOMOPS_IMAGE_CACHE") or _REPO_ROOT / "data" / "image_cache")

_DEAD_HOSTS = ("duckduckgo.com", "bing.com", "google.com")  # index pages, not images

_OFF = {"0", "off", "false", "no", "none"}


class WebImageError(RuntimeError):
    """A lookup or download failed in a way the caller may want to report."""


@dataclass(frozen=True)
class ImageHit:
    """One search result: where the picture lives and what it is."""

    title: str
    url: str
    provider: str
    page: str = ""
    license: str = ""
    width: int = 0
    height: int = 0


@dataclass(frozen=True)
class FetchedImage:
    """A downloaded, validated photo plus its provenance."""

    image: bytes
    name: str
    provider: str
    title: str = ""
    page: str = ""
    license: str = ""
    cached: bool = False

    @property
    def source(self) -> str:
        return f"web:{self.provider}" if self.provider else "web"


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

def enabled() -> bool:
    """Web lookups are on unless ``ECOMOPS_WEB_IMAGES`` says otherwise."""
    return os.environ.get("ECOMOPS_WEB_IMAGES", "on").strip().lower() not in _OFF


def cache_dir(path: str | Path | None = None) -> Path:
    target = Path(path) if path is not None else CACHE_DIR
    return target


# --------------------------------------------------------------------------- #
# Search
# --------------------------------------------------------------------------- #

def _get_json(url: str, *, timeout: float) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def search_wikimedia(query: str, *, limit: int = 6, timeout: float = TIMEOUT) -> list[ImageHit]:
    """Commons file search, restricted to raster images with a scaled thumbnail."""
    params = {
        "action": "query",
        "format": "json",
        "generator": "search",
        "gsrsearch": query,
        "gsrnamespace": "6",
        "gsrlimit": str(max(1, min(limit, 20))),
        "prop": "imageinfo",
        "iiprop": "url|mime|size|extmetadata",
        "iiurlwidth": "1000",
    }
    payload = _get_json(
        "https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode(params),
        timeout=timeout,
    )
    pages = ((payload.get("query") or {}).get("pages") or {}) if isinstance(payload, dict) else {}
    hits: list[ImageHit] = []
    for page in pages.values():
        info = (page.get("imageinfo") or [{}])[0]
        if not str(info.get("mime", "")).startswith("image/"):
            continue
        url = info.get("thumburl") or info.get("url")
        if not url:
            continue
        meta = info.get("extmetadata") or {}
        hits.append(ImageHit(
            title=str(page.get("title") or "").removeprefix("File:"),
            url=str(url),
            provider="wikimedia",
            page=str(info.get("descriptionurl") or ""),
            license=str((meta.get("LicenseShortName") or {}).get("value") or ""),
            width=int(info.get("thumbwidth") or info.get("width") or 0),
            height=int(info.get("thumbheight") or info.get("height") or 0),
        ))
    return hits


def search_openverse(query: str, *, limit: int = 6, timeout: float = TIMEOUT) -> list[ImageHit]:
    """Openverse's CC image index (no key required)."""
    params = {"q": query, "page_size": str(max(1, min(limit, 20))), "mature": "false"}
    payload = _get_json(
        "https://api.openverse.org/v1/images/?" + urllib.parse.urlencode(params),
        timeout=timeout,
    )
    results = payload.get("results") if isinstance(payload, dict) else None
    hits: list[ImageHit] = []
    for row in results or []:
        url = row.get("url") or row.get("thumbnail")
        if not url:
            continue
        hits.append(ImageHit(
            title=str(row.get("title") or ""),
            url=str(url),
            provider="openverse",
            page=str(row.get("foreign_landing_url") or ""),
            license=str(row.get("license") or ""),
            width=int(row.get("width") or 0),
            height=int(row.get("height") or 0),
        ))
    return hits


PROVIDERS = (("wikimedia", search_wikimedia), ("openverse", search_openverse))

# Words that carry no product meaning, ignored when scoring a hit.
_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "of", "for", "with", "without", "in", "on",
    "to", "by", "from", "set", "pack", "new", "best",
})

# Titles that are search bait rather than a photo of the product itself.
_BLOCKED_TITLE_WORDS = (
    "map", "diagram", "chart", "logo", "icon", "screenshot", "stamp",
    "banknote", "coin", "graph", "schema", "infographic",
)

MIN_SCORE = 0.30      # share of query words that must appear in the title


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return {word[:6] if len(word) > 5 else word for word in words} - _STOPWORDS


def relevance(hit: ImageHit, query: str) -> float:
    """How much of ``query`` the hit title echoes, from 0 to 1.

    Commons is a full-text index, so a search for "aluminium laptop stand" can
    return a flight-path map that merely mentions those words in its
    description. Scoring the *title* keeps the photo card meaningful.
    """
    wanted = _tokens(query)
    if not wanted:
        return 0.0
    return len(wanted & _tokens(hit.title)) / len(wanted)


def usable(hit: ImageHit, query: str = "", *, min_score: float = 0.0) -> bool:
    """False for junk results: index pages, diagrams, or off-topic titles."""
    if not hit.url:
        return False
    lowered = hit.title.lower()
    if any(word in lowered for word in _BLOCKED_TITLE_WORDS):
        return False
    return relevance(hit, query) >= min_score if query else True


def search_item_images(
    query: str,
    *,
    limit: int = 6,
    timeout: float = TIMEOUT,
    providers: tuple = PROVIDERS,
    min_score: float = MIN_SCORE,
) -> list[ImageHit]:
    """Search every provider in order; hits are ranked by title relevance.

    The first provider that yields a usable hit wins, so the broad CC index is
    never consulted once Commons has answered.
    """
    text = (query or "").strip()
    if not text or not enabled():
        return []
    for name, search in providers:
        try:
            raw = [hit for hit in search(text, limit=limit, timeout=timeout) if hit.url]
        except Exception as exc:
            log.debug("image search %s failed for %r: %s", name, text, exc)
            continue
        hits = [hit for hit in raw if usable(hit, text, min_score=min_score)]
        if not hits:
            log.debug("image search %s: %d hits, none relevant to %r", name, len(raw), text)
            continue
        hits.sort(key=lambda hit: relevance(hit, text), reverse=True)
        return hits
    return []


# --------------------------------------------------------------------------- #
# Download
# --------------------------------------------------------------------------- #

def _looks_like_image(data: bytes) -> bool:
    return data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n" or data[:4] == b"RIFF"


def download_image(url: str, *, timeout: float = TIMEOUT * 1.5, max_bytes: int = MAX_BYTES) -> bytes:
    """Fetch ``url`` and return the bytes if it decodes as a usable photo."""
    if any(host in url for host in _DEAD_HOSTS):
        raise WebImageError(f"refusing to download an index page: {url}")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read(max_bytes + 1)
            content_type = response.headers.get("Content-Type", "")
    except Exception as exc:
        raise WebImageError(f"download failed for {url}: {exc}") from exc

    if len(data) > max_bytes:
        raise WebImageError(f"image larger than {max_bytes // (1024 * 1024)} MB: {url}")
    if not data:
        raise WebImageError(f"empty response: {url}")
    if content_type and not content_type.lower().startswith(("image/", "application/octet-stream")):
        raise WebImageError(f"not an image ({content_type}): {url}")
    if not _looks_like_image(data) and not content_type.lower().startswith("image/"):
        raise WebImageError(f"payload is not a known image format: {url}")

    try:
        with Image.open(io.BytesIO(data)) as probe:
            probe.load()
            if min(probe.size) < MIN_EDGE:
                raise WebImageError(f"image too small ({probe.size[0]}x{probe.size[1]}): {url}")
    except WebImageError:
        raise
    except Exception as exc:
        raise WebImageError(f"undecodable image: {exc}") from exc
    return data


# --------------------------------------------------------------------------- #
# Cache
# --------------------------------------------------------------------------- #

def _slug(text: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return cleaned[:60] or "item"


def _cache_key(query: str) -> str:
    """Bump the prefix when relevance rules change, so old picks are refetched."""
    seed = f"v2\n{query.strip().lower()}"
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]


def _read_cache(folder: Path, query: str) -> FetchedImage | None:
    key = _cache_key(query)
    meta_path = folder / f"{key}.json"
    image_path = folder / f"{key}.img"
    if not (meta_path.is_file() and image_path.is_file()):
        return None
    try:
        meta = json.loads(meta_path.read_text("utf-8"))
        data = image_path.read_bytes()
    except (OSError, json.JSONDecodeError):
        return None
    return FetchedImage(
        image=data,
        name=str(meta.get("name") or query),
        provider=str(meta.get("provider") or ""),
        title=str(meta.get("title") or ""),
        page=str(meta.get("page") or ""),
        license=str(meta.get("license") or ""),
        cached=True,
    )


def _write_cache(folder: Path, query: str, fetched: FetchedImage) -> None:
    key = _cache_key(query)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{key}.img").write_bytes(fetched.image)
        (folder / f"{key}.json").write_text(json.dumps({
            "query": query,
            "name": fetched.name,
            "provider": fetched.provider,
            "title": fetched.title,
            "page": fetched.page,
            "license": fetched.license,
        }), "utf-8")
    except OSError as exc:  # pragma: no cover - a read-only cache must not break the poster
        log.debug("could not cache image for %r: %s", query, exc)


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #

_SPEC = re.compile(
    r"^\d+(?:[.,]\d+)?(?:ml|l|lt|ltr|cl|g|kg|mg|cm|mm|km|m|in|ft|w|kw|v|hz|gb|tb|mb|pcs|pc|pack|pk)$",
    re.IGNORECASE,
)


def search_queries(name: str) -> list[str]:
    """Queries to try for ``name``, most specific first.

    Catalogue names carry sizes and specs ("Insulated water bottle 750ml") that
    a photo index treats as noise, so the size tokens are dropped and finally
    only the last two words are kept ("water bottle").
    """
    text = re.sub(r"[()\[\],/]", " ", name or "").strip()
    queries: list[str] = [" ".join(text.split())] if text else []
    if not queries:
        return []
    simplified = " ".join(word for word in text.split() if not _SPEC.match(word)).strip()
    if simplified and simplified.lower() not in {query.lower() for query in queries}:
        queries.append(simplified)
    tail = " ".join(simplified.split()[-2:])
    if tail and tail.lower() not in {query.lower() for query in queries}:
        queries.append(tail)
    return queries


def fetch_item_image(
    name: str,
    *,
    folder: str | Path | None = None,
    limit: int = 6,
    timeout: float = TIMEOUT,
    providers: tuple = PROVIDERS,
) -> FetchedImage | None:
    """Best photo for ``name``, or ``None`` when nothing usable was found.

    A cached copy is returned first so repeat posters cost no network calls.
    Progressively simpler search terms are tried before giving up.
    """
    text = (name or "").strip()
    if not text or not enabled():
        return None
    store = cache_dir(folder)

    cached = _read_cache(store, text)
    if cached is not None:
        return cached

    for query in search_queries(text):
        hits = search_item_images(query, limit=limit, timeout=timeout, providers=providers)
        for hit in hits:
            try:
                data = download_image(hit.url, timeout=max(timeout, TIMEOUT))
            except WebImageError as exc:
                log.debug("skipping %s: %s", hit.url, exc)
                continue
            fetched = FetchedImage(
                image=data,
                name=text,
                provider=hit.provider,
                title=hit.title,
                page=hit.page,
                license=hit.license,
            )
            _write_cache(store, text, fetched)
            return fetched
    return None


def fetch_item_images(
    names: list[str] | tuple[str, ...],
    *,
    folder: str | Path | None = None,
    limit: int = 6,
) -> dict[str, FetchedImage]:
    """Fetch a photo for each name; missing ones are simply absent from the dict."""
    found: dict[str, FetchedImage] = {}
    for name in names:
        photo = fetch_item_image(name, folder=folder, limit=limit)
        if photo is not None:
            found[name] = photo
    return found


def describe(fetched: FetchedImage | None) -> str:
    """One-line provenance label for the UI."""
    if fetched is None:
        return "no photo found"
    bits = [fetched.source]
    if fetched.license:
        bits.append(fetched.license)
    if fetched.cached:
        bits.append("cached")
    return " · ".join(bits)


__all__ = [
    "CACHE_DIR",
    "FetchedImage",
    "ImageHit",
    "WebImageError",
    "describe",
    "download_image",
    "enabled",
    "fetch_item_image",
    "fetch_item_images",
    "relevance",
    "search_item_images",
    "search_openverse",
    "search_queries",
    "search_wikimedia",
    "usable",
]
