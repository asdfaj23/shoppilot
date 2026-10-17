"""
ShopPilot domains/ecommerce · 数据适配器
================================================================================
分层单向依赖：models → domains → harness → agents。adapter 是"业务数据换源只改这里"
的隔离层：现在读本地 CSV（教学数据），换成真实电商 API 时只改本文件的加载函数，
6 个工具的签名、schema、返回结构全部不变——这正是 week2 "业务换了，工具接口不变"的落地。
"""
from __future__ import annotations

from functools import lru_cache

import pandas as pd

from config import ECOM_DIR

BAD_RATING = 3  # rating<=3 视为差评（与 week2 口径一致）


@lru_cache(maxsize=1)
def load_products() -> pd.DataFrame:
    df = pd.read_csv(ECOM_DIR / "products.csv", encoding="utf-8")
    return df


@lru_cache(maxsize=1)
def load_reviews() -> pd.DataFrame:
    return pd.read_csv(ECOM_DIR / "reviews.csv", encoding="utf-8")


@lru_cache(maxsize=1)
def load_sales() -> pd.DataFrame:
    return pd.read_csv(ECOM_DIR / "sales.csv", encoding="utf-8")


def product_names() -> list[str]:
    return load_products()["name"].tolist()


def categories() -> list[str]:
    return sorted(load_products()["category"].unique().tolist())


def product_row(name: str) -> dict | None:
    """按名称取商品行（容错：模糊包含匹配一次）。"""
    df = load_products()
    hit = df[df["name"] == name]
    if hit.empty:
        hit = df[df["name"].str.contains(str(name), na=False)]
    return hit.iloc[0].to_dict() if not hit.empty else None
