"""Streamlit caching layer over src.core.data_access.

Wraps the framework-free read functions in src/core/data_access.py with
@st.cache_data and TTLs, so pages avoid hammering SQLite on every render.
Cache is automatically invalidated after TTL or when underlying data
changes (use clear_all_caches() or one of the targeted clear_* functions).

This module owns everything Streamlit-specific about caching; the exported
function names match what src/core/cache.py used to export, so callers
only needed an import-path change, not a rename.
"""
import streamlit as st

from src.core import data_access
from src.infrastructure.logger import get_logger

logger = get_logger(__name__)

# TTL in seconds — tune based on how often data changes
SHORT_TTL = 60       # 1 min — frequently changing data (inventory)
MEDIUM_TTL = 300     # 5 min — moderately changing (listings, prices)
LONG_TTL = 1800      # 30 min — rarely changing (categories, config)


@st.cache_data(ttl=LONG_TTL, show_spinner=False)
def get_marketplaces() -> list[str]:
    """Cached active marketplaces. Cached 30 min."""
    return data_access.get_marketplaces()


@st.cache_data(ttl=LONG_TTL, show_spinner=False)
def get_categories() -> list[str]:
    """Cached distinct product categories. Cached 30 min."""
    return data_access.get_categories()


@st.cache_data(ttl=MEDIUM_TTL, show_spinner=False)
def get_product_dropdown_cached() -> list[str]:
    """Cached product dropdown options."""
    return data_access.get_product_dropdown()


@st.cache_data(ttl=MEDIUM_TTL, show_spinner=False)
def get_pack_dropdown_cached() -> list[str]:
    """Cached pack dropdown options."""
    return data_access.get_pack_dropdown()


@st.cache_data(ttl=MEDIUM_TTL, show_spinner="Calculating profitability...")
def get_profitability_cached(marketplace: str | None = None):
    """Cached profitability calculation."""
    return data_access.get_profitability(marketplace)


@st.cache_data(ttl=MEDIUM_TTL, show_spinner="Building gap matrix...")
def get_gap_matrix_cached():
    """Cached gap matrix."""
    return data_access.get_gap_matrix()


@st.cache_data(ttl=SHORT_TTL, show_spinner=False)
def get_inventory_balances_cached():
    """Cached inventory balances. Short TTL because stock changes often."""
    return data_access.get_inventory_balances()


def clear_all_caches():
    """Call this after any write operation to invalidate caches."""
    st.cache_data.clear()
    logger.info("All Streamlit caches cleared")


def clear_inventory_cache():
    """Targeted invalidation when only inventory changes."""
    get_inventory_balances_cached.clear()
    logger.debug("Inventory cache cleared")


def clear_catalog_cache():
    """Targeted invalidation when catalog changes."""
    get_product_dropdown_cached.clear()
    get_pack_dropdown_cached.clear()
    get_profitability_cached.clear()
    get_gap_matrix_cached.clear()
    logger.debug("Catalog cache cleared")
