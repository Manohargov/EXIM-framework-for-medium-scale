"""Grocery catalogue pack: parsing, cleaning, merging and matching.

The shipped ``data/grocery_catalogue.csv`` is 1.5 MB, so these tests build a
tiny look-alike fixture with the same quirks: thousands separators in prices,
duplicate titles, junk rows, missing ratings and multi-line descriptions.
"""

import csv

import pytest

from ecomops.catalogue import (
    load_grocery_catalogue,
    merge_catalogues,
    parse_discount,
    parse_price,
    parse_rating,
)
from ecomops.inventory import CATALOGUE, Product
from ecomops.llm import TokenOverlapEmbedder
from ecomops.rules import capture_intent
from ecomops.system import build_system

COLUMNS = [
    "Sub Category", "Price", "Discount", "Rating", "Title",
    "Currency", "Feature", "Product Description",
]

ROWS = [
    {
        "Sub Category": "Bakery & Desserts",
        "Price": "$56.99 ",
        "Discount": "No Discount",
        "Rating": "Rated 4.3 out of 5 stars based on 265 reviews.",
        "Title": "David's Cookies Mile High Peanut Butter Cake, 6.8 lbs (14 Servings)",
        "Currency": "$",
        "Feature": '10" cake\nKosher',
        "Product Description": "Fudge brownie base, peanut butter mousse.\nServes 14.",
    },
    {   # duplicate of row 1, differing only in case/whitespace
        "Sub Category": "Bakery & Desserts",
        "Price": "$61.99 ",
        "Discount": "No Discount",
        "Rating": "Rated 4.0 out of 5 stars based on 9 reviews.",
        "Title": "  david's cookies mile high peanut butter cake, 6.8 lbs (14 servings) ",
        "Currency": "$",
        "Feature": "",
        "Product Description": "duplicate listing",
    },
    {
        "Sub Category": "Snacks",
        "Price": "$1,999.99 ",
        "Discount": "After $5 - $8 OFF",
        "Rating": "Rated 4.5 out of 5 stars based on 1,234 reviews.",
        "Title": "Giant Pretzel Tub, 2 lbs",
        "Currency": "$",
        "Feature": "salty",
        "Product Description": "Crunchy.",
    },
    {
        "Sub Category": "Bakery & Desserts",
        "Price": "$0.00",
        "Discount": "No Discount",
        "Rating": "",
        "Title": "Page Not Found!",
        "Currency": "$",
        "Feature": "",
        "Product Description": "",
    },
    {
        "Sub Category": "Household",
        "Price": "",
        "Discount": "No Discount",
        "Rating": "",
        "Title": "Household item without a price",
        "Currency": "$",
        "Feature": "",
        "Product Description": "",
    },
    {
        "Sub Category": "Household",
        "Price": "$12.50",
        "Discount": "No Discount",
        "Rating": "",
        "Title": "Paper Towels 12-pack",
        "Currency": "$",
        "Feature": "2-ply",
        "Product Description": "Soft and absorbent.",
    },
]


@pytest.fixture
def grocery_csv(tmp_path):
    path = tmp_path / "grocery.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(ROWS)
    return path


# ------------------------------------------------------------------ parsing

def test_parse_price_handles_currency_and_thousands():
    assert parse_price("$56.99 ") == 56.99
    assert parse_price("$1,999.99 ") == 1999.99
    assert parse_price("") is None
    assert parse_price("free") is None


def test_parse_discount_reads_headline_saving():
    assert parse_discount("No Discount") == 0.0
    assert parse_discount("After $5 OFF") == 5.0
    assert parse_discount("After $5 - $8 OFF") == 8.0


def test_parse_rating_extracts_score_and_review_count():
    assert parse_rating("Rated 4.3 out of 5 stars based on 265 reviews.") == (4.3, 265)
    assert parse_rating("Rated 4.5 out of 5 stars based on 1,234 reviews.") == (4.5, 1234)
    assert parse_rating("") == (None, 0)


# ------------------------------------------------------------------- loading

def test_load_dedupes_skips_and_cleans(grocery_csv):
    products, report = load_grocery_catalogue(grocery_csv)

    assert report.rows == 6
    assert report.loaded == 3
    assert report.duplicates == 1
    assert report.skipped == 2           # Page Not Found! + no price
    assert report.categories == {"Bakery & Desserts": 1, "Household": 1, "Snacks": 1}
    assert [p.sku for p in products] == ["GRO-0001", "GRO-0002", "GRO-0003"]

    cake, towels, pretzel = products
    assert cake.name.startswith("David's Cookies")
    assert cake.unit_price == 56.99 and cake.rating == 4.3 and cake.reviews == 265
    assert cake.description == "Fudge brownie base, peanut butter mousse. Serves 14."
    assert "Kosher" in cake.features
    assert cake.source == "grocery" and cake.currency == "$"

    assert towels.name == "Paper Towels 12-pack" and towels.rating is None
    assert pretzel.unit_price == 1999.99 and pretzel.discount == 8.0
    assert pretzel.rating == 4.5 and pretzel.reviews == 1234


def test_load_is_deterministic(grocery_csv):
    first, _ = load_grocery_catalogue(grocery_csv)
    second, _ = load_grocery_catalogue(grocery_csv)
    key = lambda p: (p.sku, p.name, p.unit_price, p.stock, p.age_days)  # noqa: E731
    assert [key(p) for p in first] == [key(p) for p in second]


def test_limit_truncates_but_keeps_identical_skus(grocery_csv):
    full, _ = load_grocery_catalogue(grocery_csv)
    capped, report = load_grocery_catalogue(grocery_csv, limit=2)

    assert report.loaded == 2 and report.truncated == 1
    assert [p.sku for p in capped] == [p.sku for p in full[:2]]


def test_missing_file_is_a_clear_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="grocery catalogue not found"):
        load_grocery_catalogue(tmp_path / "nope.csv")


def test_merge_drops_duplicate_skus():
    a = [Product("GRO-0001", "one", "x", 1.0, 1)]
    b = [Product("GRO-0001", "dupe", "x", 2.0, 1), Product("GRO-0002", "two", "x", 3.0, 1)]
    assert [p.name for p in merge_catalogues(a, b)] == ["one", "two"]


# --------------------------------------------------------------- integration

def test_system_merges_grocery_next_to_demo_catalogue(grocery_csv):
    system = build_system(grocery=grocery_csv)

    assert len(system.inventory.products) == len(CATALOGUE) + 3
    assert system.grocery_report is not None and system.grocery_report.loaded == 3
    assert system.inventory.products["SKU-1001"].source == "demo"

    product, score = system.inventory.match("paper towels")
    assert product is not None and product.sku == "GRO-0002"
    assert score > 0.6


def test_collective_matching_still_rejects_unrelated_queries(grocery_csv):
    system = build_system(grocery=grocery_csv)
    product, score = system.inventory.match("quantum flux capacitor")
    assert product is None and score < 0.25


async def test_stock_report_caps_the_clearance_pass(grocery_csv):
    system = build_system(grocery=grocery_csv)
    for product in system.inventory.products.values():
        product.stock, product.age_days = 500, 400

    await system.report_stock_levels(max_items=5)

    report = system.bus.of_type("StockLevelReport")[0]
    assert len(report.overstocked) == 5
    assert report.total_overstocked == len(CATALOGUE) + 3


def test_customer_phrasing_reaches_the_matcher():
    out = capture_intent(
        {"request_text": "I need 2 jars of peanut butter to Berlin ASAP", "catalogue": []}
    )
    assert out["items"] == [{"query": "peanut butter", "quantity": 2}]
    assert out["destination"] == "Berlin"
    assert out["intent_type"] == "purchase"


def test_short_query_matches_long_catalogue_title():
    embedder = TokenOverlapEmbedder()
    title = "Bounty Advanced Paper Towels, 2-Ply, 101 Sheets, 12-count"
    assert embedder.similarity("paper towels", title) > 0.6
    assert embedder.similarity("quantum flux capacitor", title) < 0.25


class _BatchingEmbedder:
    """Minimal stand-in for the MiniLM batched path (torch is not installed)."""

    name = "fake-batch"

    def __init__(self) -> None:
        self.batches = 0
        self.singles = 0

    def similarity(self, a, b):
        self.singles += 1
        return 0.0

    def similarities(self, query: str, texts: list[str]) -> list[float]:
        self.batches += 1
        return [1.0 if text == "target" else 0.0 for text in texts]


def test_inventory_uses_batched_embedder_when_available():
    from ecomops.inventory import Inventory

    embedder = _BatchingEmbedder()
    inventory = Inventory.with_catalogue(embedder, [
        Product("A", "other", "x", 1.0, 1),
        Product("B", "target", "x", 2.0, 1),
    ])

    product, score = inventory.match("anything")
    assert product is not None and product.sku == "B" and score == 1.0
    assert embedder.batches == 1 and embedder.singles == 0


# ---------------------------------------------------------------------- API

def test_api_exposes_merged_catalogue_and_matcher(grocery_csv):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from ecomops.api import create_app

    with TestClient(create_app(build_system(grocery=grocery_csv), require_auth=False)) as client:
        inventory = client.get("/inventory").json()
        assert inventory["count"] == len(CATALOGUE) + 3
        assert inventory["by_source"] == {"demo": len(CATALOGUE), "grocery": 3}
        assert inventory["grocery"]["loaded"] == 3

        match = client.get("/catalogue/match", params={"query": "paper towels"}).json()
        assert match["matched"] is True
        assert match["best"]["sku"] == "GRO-0002"
        assert match["candidates"][0]["score"] > 0.6

        miss = client.get("/catalogue/match", params={"query": "quantum flux capacitor"}).json()
        assert miss["matched"] is False
