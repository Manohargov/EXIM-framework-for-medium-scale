"""Catalogue sources.

The built-in demo catalogue is ten electronics/outdoors SKUs — small enough to
read in an event trace. For interface testing we can additionally merge a real
grocery catalogue: the 1,757-row ``GroceryDataset.csv`` export with prices,
discounts, ratings and marketing copy across 19 sub-categories.

This is *demo data*, not a trained model. Loading is deterministic (same file in
-> same products out), so UI screenshots, API responses and tests stay
reproducible:

* prices are parsed to floats (``$1,999.99 `` -> ``1999.99``);
* ``After $5 OFF`` becomes a numeric discount, ``No Discount`` becomes ``0``;
* ratings/review counts are extracted from
  ``Rated 4.3 out of 5 stars based on 265 reviews.``;
* duplicate titles (250 in the shipped file) are collapsed to one product;
* junk rows (``Page Not Found!``, no usable price) are skipped;
* stock and age are derived from a stable hash of the title, so the catalogue
  exercises healthy, ageing and out-of-stock lines without a warehouse feed.

The grocery rows are merged *next to* the demo catalogue, never replacing it:
the ten demo SKUs keep the scenario texts and existing tests meaningful.
"""

from __future__ import annotations

import csv
import hashlib
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .inventory import Product

# The shipped file lives next to the project, not inside the package.
DEFAULT_GROCERY_PATH = Path(__file__).resolve().parents[2] / "data" / "grocery_catalogue.csv"

_MAX_DESCRIPTION = 2000
_MAX_FEATURES = 600
_JUNK_TITLES = {"page not found", "page not found!", "n/a", "na", ""}

_PRICE = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?")
_DISCOUNT = re.compile(r"\$\s*(\d+(?:\.\d+)?)\s*(?:-\s*\$?\s*(\d+(?:\.\d+)?))?\s*OFF", re.IGNORECASE)
_RATING = re.compile(r"([0-5](?:\.\d+)?)\s*out of\s*5", re.IGNORECASE)
_REVIEWS = re.compile(r"based on\s+([\d,]+)\s*review", re.IGNORECASE)


@dataclass(frozen=True)
class GroceryLoadReport:
    """What the loader did with the CSV — shown in the CLI and the UI."""

    path: Path
    rows: int
    loaded: int
    duplicates: int = 0
    skipped: int = 0
    truncated: int = 0
    categories: dict[str, int] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return (
            f"{self.path.name}: {self.loaded} products from {self.rows} rows "
            f"({self.duplicates} duplicates, {self.skipped} skipped"
            + (f", {self.truncated} over limit" if self.truncated else "")
            + ")"
        )


# --------------------------------------------------------------------------- #
# Field parsers (exported for tests and ad-hoc inspection)
# --------------------------------------------------------------------------- #


def parse_price(text: str | None) -> float | None:
    """``'$1,999.99 '`` -> ``1999.99``; ``''`` / junk -> ``None``."""
    match = _PRICE.search(text or "")
    if match is None:
        return None
    try:
        value = float(match.group(0).replace(",", ""))
    except ValueError:  # pragma: no cover - the regex only matches numerals
        return None
    return value if value > 0 else None


def parse_discount(text: str | None) -> float:
    """``'After $5 OFF'`` -> ``5.0``; ranges use the larger headline saving."""
    match = _DISCOUNT.search(text or "")
    if match is None:
        return 0.0
    amounts = [float(group) for group in match.groups() if group]
    return max(amounts)


def parse_rating(text: str | None) -> tuple[float | None, int]:
    """``'Rated 4.3 out of 5 stars based on 265 reviews.'`` -> ``(4.3, 265)``."""
    raw = text or ""
    rating_match = _RATING.search(raw)
    if rating_match is None:
        return None, 0
    rating = float(rating_match.group(1))
    reviews_match = _REVIEWS.search(raw)
    reviews = int(reviews_match.group(1).replace(",", "")) if reviews_match else 0
    return rating, reviews


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #


def _clean(text: str | None, *, limit: int | None = None) -> str:
    value = " ".join((text or "").split())
    if limit is not None and len(value) > limit:
        value = value[: limit - 1].rstrip() + "…"
    return value


def _stock_and_age(key: str) -> tuple[int, int]:
    """Deterministic pseudo-stock: ~8% out of stock, the rest spread out."""
    digest = hashlib.blake2s(key.encode("utf-8"), digest_size=8).digest()
    stock = 5 + int.from_bytes(digest[0:2], "big") % 245
    if digest[4] % 12 == 0:
        stock = 0
    age = 4 + int.from_bytes(digest[2:4], "big") % 361
    return stock, age


def load_grocery_catalogue(
    path: str | Path,
    *,
    limit: int | None = None,
) -> tuple[list[Product], GroceryLoadReport]:
    """Clean the grocery CSV into catalogue products.

    ``limit`` keeps the first N products after de-duplication and sorting, which
    is handy for a lighter UI.  IDs are assigned after sorting, so the same file
    always yields the same ``GRO-####`` SKUs regardless of ``limit``.
    """
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(
            f"grocery catalogue not found: {source} "
            "(pass --grocery PATH or set ECOMOPS_GROCERY)"
        )

    with source.open(newline="", encoding="utf-8-sig", errors="replace") as handle:
        rows = list(csv.DictReader(handle))

    seen: set[str] = set()
    products: list[Product] = []
    duplicates = 0
    skipped = 0

    for row in rows:
        title = _clean(row.get("Title"))
        name_key = re.sub(r"\s+", " ", title.casefold()).strip()
        if name_key in _JUNK_TITLES:
            skipped += 1
            continue
        price = parse_price(row.get("Price"))
        if price is None:
            skipped += 1
            continue
        if name_key in seen:
            duplicates += 1
            continue
        seen.add(name_key)

        rating, reviews = parse_rating(row.get("Rating"))
        category = _clean(row.get("Sub Category")) or "grocery"
        stock, age = _stock_and_age(name_key)

        products.append(
            Product(
                sku="",  # assigned below, after sorting
                name=title,
                category=category,
                unit_price=round(price, 2),
                stock=stock,
                age_days=age,
                rating=rating,
                reviews=reviews,
                discount=parse_discount(row.get("Discount")),
                description=_clean(row.get("Product Description"), limit=_MAX_DESCRIPTION),
                features=_clean(row.get("Feature"), limit=_MAX_FEATURES),
                currency=_clean(row.get("Currency")) or "$",
                source="grocery",
            )
        )

    products.sort(key=lambda p: (p.category.casefold(), p.name.casefold()))
    truncated = 0
    if limit is not None and limit >= 0 and len(products) > limit:
        truncated = len(products) - limit
        products = products[:limit]
    for index, product in enumerate(products, start=1):
        product.sku = f"GRO-{index:04d}"

    categories = Counter(p.category for p in products)
    report = GroceryLoadReport(
        path=source,
        rows=len(rows),
        loaded=len(products),
        duplicates=duplicates,
        skipped=skipped,
        truncated=truncated,
        categories=dict(categories.most_common()),
    )
    return products, report


def merge_catalogues(*catalogues: list[Product]) -> list[Product]:
    """Concatenate catalogues, dropping later rows that reuse a SKU."""
    merged: list[Product] = []
    seen: set[str] = set()
    for catalogue in catalogues:
        for product in catalogue:
            if product.sku in seen:
                continue
            seen.add(product.sku)
            merged.append(product)
    return merged


__all__ = [
    "DEFAULT_GROCERY_PATH",
    "GroceryLoadReport",
    "load_grocery_catalogue",
    "merge_catalogues",
    "parse_discount",
    "parse_price",
    "parse_rating",
]
