"""Shared pytest fixtures. Everything here runs offline (no LLM network calls)."""
from __future__ import annotations

import pytest

from shoppilot.core.settings import REPO_ROOT
from shoppilot.domains.ecommerce.adapter import CsvEcommerceAdapter, configure

ECOM_DIR = REPO_ROOT / "data" / "ecommerce"


@pytest.fixture
def ecom_adapter():
    """The bundled CSV adapter, wired as the active module-level data source."""
    adapter = CsvEcommerceAdapter(ECOM_DIR)
    configure(adapter)
    return adapter
