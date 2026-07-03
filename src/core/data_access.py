"""Framework-free read functions for read-heavy database queries.

These are the plain, uncached versions of the app's read-heavy queries.
Caching is a presentation concern and lives in src/ui/cache_adapter.py,
which wraps these functions with @st.cache_data — this module imports no
UI framework, so it can be called from a script, a cron job, or a test
with no Streamlit runtime involved.
"""
import pandas as pd
from src.infrastructure.database import get_engine
from src.infrastructure.logger import get_logger

logger = get_logger(__name__)


def get_marketplaces() -> list[str]:
    """Return active marketplaces from the SQL config table (authoritative source, E9)."""
    try:
        engine = get_engine()
        df = pd.read_sql("SELECT marketplace FROM config ORDER BY marketplace", engine)
        if df.empty or 'marketplace' not in df.columns:
            logger.warning("No marketplaces in config table; falling back to defaults")
            return ["Amazon", "Flipkart", "Meesho"]
        return df['marketplace'].dropna().unique().tolist()
    except Exception as e:
        logger.error(f"get_marketplaces failed: {e}", exc_info=True)
        return ["Amazon", "Flipkart", "Meesho"]


def get_rules_last_verified() -> str | None:
    """Return the ISO datetime string when fee rules were last verified, or None."""
    try:
        from sqlalchemy import text
        engine = get_engine()
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT value FROM rules_meta WHERE key = 'rules_last_verified'")
            ).fetchone()
            return row[0] if row else None
    except Exception as e:
        logger.error(f"get_rules_last_verified failed: {e}", exc_info=True)
        return None


def set_rules_last_verified() -> None:
    """Record the current UTC time as when rules were last verified."""
    from datetime import datetime, timezone
    from sqlalchemy import text
    ts = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    try:
        engine = get_engine()
        with engine.begin() as conn:
            conn.execute(
                text("INSERT OR REPLACE INTO rules_meta (key, value) VALUES ('rules_last_verified', :ts)"),
                {"ts": ts},
            )
    except Exception as e:
        logger.error(f"set_rules_last_verified failed: {e}", exc_info=True)


def get_categories() -> list[str]:
    """Return distinct product categories."""
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


def get_product_dropdown() -> list[str]:
    """Product dropdown options, via CatalogService."""
    from src.core.services.catalog_service import CatalogService
    return CatalogService().get_product_dropdown()


def get_pack_dropdown() -> list[str]:
    """Pack dropdown options, via CatalogService."""
    from src.core.services.catalog_service import CatalogService
    return CatalogService().get_pack_dropdown()


def get_profitability(marketplace: str | None = None) -> pd.DataFrame:
    """Profitability calculation, via FinanceService."""
    from src.core.services.finance_service import FinanceService
    return FinanceService().calculate_profitability(marketplace_filter=marketplace)


def get_gap_matrix() -> pd.DataFrame:
    """Gap matrix (pack x marketplace listing coverage), via GapService."""
    from src.core.services.gap_service import GapService
    return GapService().get_gap_matrix()


def get_inventory_balances() -> pd.DataFrame:
    """Inventory balances joined with product names."""
    engine = get_engine()
    return pd.read_sql("""
        SELECT im.sku, p.name, im.godown_stock_packs, im.shop_stock_pieces,
               im.pack_multiplier, im.last_updated
        FROM inventory_master im
        LEFT JOIN product_master p ON im.sku = p.sku
        ORDER BY im.last_updated DESC
    """, engine)
