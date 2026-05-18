"""Centralized caching layer for read-heavy database queries.

Uses Streamlit's @st.cache_data with TTL to avoid hammering SQLite
on every page render. Cache is automatically invalidated after TTL
or when underlying data changes (use clear_all_caches()).
"""
import pandas as pd
import streamlit as st
from src.infrastructure.database import get_engine
from src.infrastructure.logger import get_logger

logger = get_logger(__name__)

# TTL in seconds — tune based on how often data changes
SHORT_TTL = 60       # 1 min — frequently changing data (inventory)
MEDIUM_TTL = 300     # 5 min — moderately changing (listings, prices)
LONG_TTL = 1800      # 30 min — rarely changing (categories, config)


@st.cache_data(ttl=LONG_TTL, show_spinner=False)
def get_marketplaces() -> list[str]:
    """Return active marketplaces from config table. Cached 30 min."""
    try:
        engine = get_engine()
        df = pd.read_sql("SELECT marketplace FROM config ORDER BY marketplace", engine)
        if df.empty:
            logger.warning("No marketplaces in config; falling back to defaults")
            return ["Amazon", "Flipkart", "Meesho"]
        return df['marketplace'].tolist()
    except Exception as e:
        logger.error(f"get_marketplaces failed: {e}", exc_info=True)
        return ["Amazon", "Flipkart", "Meesho"]


@st.cache_data(ttl=LONG_TTL, show_spinner=False)
def get_categories() -> list[str]:
    """Return distinct product categories. Cached 30 min."""
    try:
        engine = get_engine()
        df = pd.read_sql(
            "SELECT DISTINCT category FROM product_master WHERE category IS NOT NULL ORDER BY category",
            engine
        )
        return df['category'].tolist()
    except Exception as e:
        logger.error(f"get_categories failed: {e}", exc_info=True)
        return []


@st.cache_data(ttl=MEDIUM_TTL, show_spinner=False)
def get_product_dropdown_cached() -> list[str]:
    """Cached version of CatalogService.get_product_dropdown."""
    from src.core.services.catalog_service import CatalogService
    return CatalogService().get_product_dropdown()


@st.cache_data(ttl=MEDIUM_TTL, show_spinner=False)
def get_pack_dropdown_cached() -> list[str]:
    """Cached version of CatalogService.get_pack_dropdown."""
    from src.core.services.catalog_service import CatalogService
    return CatalogService().get_pack_dropdown()


@st.cache_data(ttl=MEDIUM_TTL, show_spinner="Calculating profitability...")
def get_profitability_cached(marketplace: str | None = None) -> pd.DataFrame:
    """Cached profitability calculation."""
    from src.core.services.finance_service import FinanceService
    return FinanceService().calculate_profitability(marketplace_filter=marketplace)


@st.cache_data(ttl=MEDIUM_TTL, show_spinner="Building gap matrix...")
def get_gap_matrix_cached() -> pd.DataFrame:
    """Cached gap matrix."""
    from src.core.services.gap_service import GapService
    return GapService().get_gap_matrix()


@st.cache_data(ttl=SHORT_TTL, show_spinner=False)
def get_inventory_balances_cached() -> pd.DataFrame:
    """Cached inventory balances. Short TTL because stock changes often."""
    engine = get_engine()
    return pd.read_sql("""
        SELECT im.sku, p.name, im.godown_stock_packs, im.shop_stock_pieces,
               im.pack_multiplier, im.last_updated
        FROM inventory_master im
        LEFT JOIN product_master p ON im.sku = p.sku
        ORDER BY im.last_updated DESC
    """, engine)


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