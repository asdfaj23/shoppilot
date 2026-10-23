"""E-commerce data adapter — the single swap point for the data source.

Dependency direction: core <- domains <- tools. The bundled
:class:`CsvEcommerceAdapter` reads local CSV fixtures (the reproducible demo
dataset). To point ShopPilot at a real store, implement the
:class:`DataAdapter` Protocol against your own DB/API and call
:func:`configure` once at startup — the six tools, the recommender and the RAG
grounding all keep their signatures and return shapes unchanged.

Module-level helpers (``load_products`` ...) delegate to the *currently
configured* adapter, so existing tool code stays untouched and tests can swap in
a temporary dataset.
"""
from __future__ import annotations

from functools import cached_property
from pathlib import Path
from typing import Protocol, runtime_checkable

import pandas as pd

from shoppilot.core.settings import ECOM_DIR

BAD_RATING = 3  # rating <= 3 counts as a negative review (stable shared cutoff)


@runtime_checkable
class DataAdapter(Protocol):
    """Contract any e-commerce data source must satisfy."""

    bad_rating: int

    def load_products(self) -> pd.DataFrame: ...
    def load_reviews(self) -> pd.DataFrame: ...
    def load_sales(self) -> pd.DataFrame: ...
    def product_names(self) -> list[str]: ...
    def categories(self) -> list[str]: ...
    def product_row(self, name: str) -> dict | None: ...


class CsvEcommerceAdapter:
    """Read the bundled CSV fixture set (products / reviews / sales)."""

    def __init__(self, data_dir: Path | str, bad_rating: int = BAD_RATING):
        self.data_dir = Path(data_dir)
        self.bad_rating = bad_rating

    @cached_property
    def products(self) -> pd.DataFrame:
        return pd.read_csv(self.data_dir / "products.csv", encoding="utf-8")

    @cached_property
    def reviews(self) -> pd.DataFrame:
        return pd.read_csv(self.data_dir / "reviews.csv", encoding="utf-8")

    @cached_property
    def sales(self) -> pd.DataFrame:
        return pd.read_csv(self.data_dir / "sales.csv", encoding="utf-8")

    def load_products(self) -> pd.DataFrame:
        return self.products

    def load_reviews(self) -> pd.DataFrame:
        return self.reviews

    def load_sales(self) -> pd.DataFrame:
        return self.sales

    def product_names(self) -> list[str]:
        return self.products["name"].tolist()

    def categories(self) -> list[str]:
        return sorted(self.products["category"].unique().tolist())

    def product_row(self, name: str) -> dict | None:
        """Exact match, then a single fuzzy contains-match fallback."""
        df = self.products
        hit = df[df["name"] == name]
        if hit.empty:
            hit = df[df["name"].str.contains(str(name), na=False)]
        return hit.iloc[0].to_dict() if not hit.empty else None


# ---------------------------------------------------------------------------
# Currently-configured adapter + module-level delegation (back-compat for tools).
# ---------------------------------------------------------------------------
_default: DataAdapter = CsvEcommerceAdapter(ECOM_DIR)


def configure(adapter: DataAdapter) -> None:
    """Swap the active data source at startup (real API, test fixture, ...)."""
    global _default
    _default = adapter


def get_adapter() -> DataAdapter:
    return _default


def load_products() -> pd.DataFrame:
    return get_adapter().load_products()


def load_reviews() -> pd.DataFrame:
    return get_adapter().load_reviews()


def load_sales() -> pd.DataFrame:
    return get_adapter().load_sales()


def product_names() -> list[str]:
    return get_adapter().product_names()


def categories() -> list[str]:
    return get_adapter().categories()


def product_row(name: str) -> dict | None:
    return get_adapter().product_row(name)
