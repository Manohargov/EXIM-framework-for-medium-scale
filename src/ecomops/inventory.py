"""Inventory: catalogue, stock levels, ageing, reservations.

In-memory and process-local on purpose — this project is about agent
coordination, not about being a warehouse system of record. Every mutation
happens only after the approval gate has authorised the action.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from .llm import Embedder


@dataclass
class Product:
    sku: str
    name: str
    category: str
    unit_price: float
    stock: int
    age_days: int = 30           # days since the batch was received

    # Optional catalogue detail. The demo SKUs leave these empty; the grocery
    # pack fills them from the CSV so the UI/API have something realistic to show.
    rating: float | None = None
    reviews: int = 0
    discount: float = 0.0        # headline markdown from the source listing
    description: str = ""
    features: str = ""
    currency: str = "$"
    source: str = "demo"         # demo | grocery

    @property
    def inventory_value(self) -> float:
        return self.stock * self.unit_price

    @property
    def in_stock(self) -> bool:
        return self.stock > 0


@dataclass
class Reservation:
    reservation_id: str
    sku: str
    quantity: int
    customer_id: str
    order_id: str


# A small but realistic catalogue: fast movers next to ageing stock.
CATALOGUE = [
    Product("SKU-1001", "wireless bluetooth earbuds", "electronics", 59.99, 240, age_days=210),
    Product("SKU-1002", "usb-c fast charging cable", "electronics", 12.99, 900, age_days=45),
    Product("SKU-1003", "aluminium laptop stand", "accessories", 34.50, 60, age_days=350),
    Product("SKU-1004", "insulated water bottle 750ml", "outdoors", 24.00, 180, age_days=120),
    Product("SKU-1005", "mechanical keyboard", "electronics", 89.00, 15, age_days=20),
    Product("SKU-1006", "ergonomic office mouse", "electronics", 45.00, 130, age_days=95),
    Product("SKU-1007", "noise cancelling headphones", "electronics", 199.00, 45, age_days=60),
    Product("SKU-1008", "hdmi cable 2m", "electronics", 9.99, 1200, age_days=400),
    Product("SKU-1009", "desk lamp led", "home", 29.99, 75, age_days=180),
    Product("SKU-1010", "backpack 25l", "outdoors", 54.00, 95, age_days=75),
]


@dataclass
class Inventory:
    embedder: Embedder
    products: dict[str, Product] = field(default_factory=dict)
    reservations: dict[str, Reservation] = field(default_factory=dict)
    _seq: int = 0

    @classmethod
    def with_catalogue(cls, embedder: Embedder, products: list[Product] | None = None) -> Inventory:
        """Build an inventory from ``products`` (default: the demo catalogue).

        Rows are copied: ``CATALOGUE`` is module-level, and price/stock
        mutations must not leak into another Inventory (or the next test).
        """
        inv = cls(embedder=embedder)
        inv.add_products(CATALOGUE if products is None else products)
        return inv

    @classmethod
    def with_default_catalogue(cls, embedder: Embedder) -> Inventory:
        return cls.with_catalogue(embedder)

    def add_products(self, products: list[Product]) -> int:
        """Merge extra products, ignoring SKUs already present. Returns added count."""
        added = 0
        for p in products:
            if p.sku in self.products:
                continue
            self.products[p.sku] = replace(p)
            added += 1
        return added

    # ------------------------------------------------------------- matching

    def catalogue_names(self) -> list[str]:
        return [p.name for p in self.products.values()]

    def _similarities(self, query: str, products: list[Product]) -> list[float]:
        """Batch when the embedder supports it (MiniLM); else score one by one."""
        batch = getattr(self.embedder, "similarities", None)
        if callable(batch):
            return list(batch(query, [p.name for p in products]))
        return [self.embedder.similarity(query, p.name) for p in products]

    def rank(self, query: str, products: list[Product] | None = None) -> list[tuple[Product, float]]:
        """All products ordered by similarity to ``query`` (highest first)."""
        candidates = list(self.products.values()) if products is None else products
        scored = zip(candidates, self._similarities(query, candidates), strict=True)
        return sorted(scored, key=lambda pair: pair[1], reverse=True)

    def match(self, query: str, *, threshold: float = 0.25) -> tuple[Product | None, float]:
        """Best catalogue match for free text. Returns (product, score)."""
        ranked = self.rank(query)
        if not ranked:
            return None, 0.0
        best, best_score = ranked[0]
        if best_score >= threshold:
            return best, best_score
        return None, best_score

    # ------------------------------------------------------------ clearance

    def overstocked(self, *, min_age_days: int = 90, min_stock: int = 100) -> list[Product]:
        return [
            p for p in self.products.values()
            if p.age_days >= min_age_days or p.stock >= min_stock
        ]

    def substitutes_for(self, product: Product, *, top: int = 3) -> list[Product]:
        """Nearest same-category products — candidates for substitution/clearance."""
        candidates = [
            p for p in self.products.values()
            if p.sku != product.sku and p.category == product.category and p.stock > 0
        ]
        return [p for p, _score in self.rank(product.name, candidates)[:top]]

    # ---------------------------------------------------------- transactions

    def apply_discount(self, sku: str, discount_pct: float) -> tuple[float, float]:
        p = self.products[sku]
        previous = p.unit_price
        p.unit_price = round(p.unit_price * (1 - discount_pct / 100.0), 2)
        return previous, p.unit_price

    def reserve(self, sku: str, quantity: int, customer_id: str) -> Reservation | None:
        p = self.products[sku]
        if quantity > p.stock:
            return None
        p.stock -= quantity
        self._seq += 1
        res = Reservation(f"RES-{self._seq:04d}", sku, quantity, customer_id, f"ORD-{self._seq:04d}")
        self.reservations[res.reservation_id] = res
        return res

    def release(self, reservation_id: str) -> None:
        res = self.reservations.pop(reservation_id, None)
        if res is not None:
            self.products[res.sku].stock += res.quantity

    def restock(self, sku: str, quantity: int) -> int:
        """Put returned, resaleable units back on the shelf; returns new stock."""
        p = self.products[sku]
        p.stock += quantity
        return p.stock
