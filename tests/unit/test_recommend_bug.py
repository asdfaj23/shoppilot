"""Regression test for the ``recommend_products`` NameError bug.

The slow-delivery warning used ``warnings.append(...)`` before ``warnings`` was
initialised, so any product with delivery_days >= 4 crashed the function.
A deterministic rule must never crash on the very case it is meant to flag.
"""
from __future__ import annotations

import json

import pandas as pd

from shoppilot.domains.ecommerce.adapter import CsvEcommerceAdapter, configure
from shoppilot.domains.ecommerce.recommend import recommend_products


def test_delivery_days_ge4_emits_warning_instead_of_crashing(tmp_path):
    products = pd.DataFrame([
        {"name": "慢递键盘X", "category": "电脑外设", "price": 199, "rating": 4.2,
         "sales_month": 100, "warranty_months": 12, "delivery_days": 4,
         "highlights": "机械轴；可插拔"},
        {"name": "快键盘Y", "category": "电脑外设", "price": 299, "rating": 4.5,
         "sales_month": 200, "warranty_months": 12, "delivery_days": 1,
         "highlights": "静音；轻薄"},
    ])
    products.to_csv(tmp_path / "products.csv", index=False, encoding="utf-8")
    pd.DataFrame(columns=["product", "rating", "aspect", "date", "text"]).to_csv(
        tmp_path / "reviews.csv", index=False, encoding="utf-8")

    configure(CsvEcommerceAdapter(tmp_path))
    out = json.loads(recommend_products(category="电脑外设", budget_max=1000))
    assert "error" not in out
    by_name = {r["name"]: r for r in out["recommendations"]}
    assert "慢递键盘X" in by_name
    assert any("物流时效" in w for w in by_name["慢递键盘X"]["warnings"])
