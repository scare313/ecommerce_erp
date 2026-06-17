"""Dashboard service for Home/KPI aggregation.

Aggregates key business metrics across catalog, listings, profitability,
gaps, and inventory for the Home Dashboard.
"""
import pandas as pd
from sqlalchemy import text
from src.infrastructure.database import get_engine
from src.infrastructure.logger import get_logger, DatabaseException, ServiceException

logger = get_logger(__name__)


class DashboardService:
    """Service for aggregating Home/KPI dashboard metrics."""

    def __init__(self):
        """Initialize dashboard service with database engine."""
        try:
            self.engine = get_engine()
            logger.debug("DashboardService initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize DashboardService: {str(e)}", exc_info=True)
            raise

    def get_catalog_kpis(self):
        """
        Get catalog-level KPIs: product, pack, listing counts.

        Returns:
            dict: {total_products, total_packs, total_listings, active_listings, marketplaces}
        """
        try:
            logger.debug("Fetching catalog KPIs...")
            with self.engine.connect() as conn:
                total_products = conn.execute(
                    text("SELECT COUNT(*) FROM product_master")
                ).scalar() or 0

                total_packs = conn.execute(
                    text("SELECT COUNT(*) FROM pack_master")
                ).scalar() or 0

                total_listings = conn.execute(
                    text("SELECT COUNT(*) FROM channel_listings")
                ).scalar() or 0

                active_listings = conn.execute(
                    text("SELECT COUNT(*) FROM channel_listings WHERE LOWER(listing_status) = 'active'")
                ).scalar() or 0

                marketplaces = conn.execute(
                    text("SELECT COUNT(DISTINCT marketplace) FROM channel_listings")
                ).scalar() or 0

            kpis = {
                "total_products": total_products,
                "total_packs": total_packs,
                "total_listings": total_listings,
                "active_listings": active_listings,
                "marketplaces": marketplaces,
            }
            logger.info(f"Catalog KPIs: {kpis}")
            return kpis
        except Exception as e:
            logger.error(f"Error fetching catalog KPIs: {str(e)}", exc_info=True)
            raise DatabaseException(f"Catalog KPI fetch failed: {str(e)}") from e

    def get_listings_by_marketplace(self):
        """
        Get listing counts grouped by marketplace.

        Returns:
            DataFrame: marketplace, listing_count, avg_price
        """
        try:
            logger.debug("Fetching listings by marketplace...")
            query = """
                SELECT
                    marketplace,
                    COUNT(*) as listing_count,
                    AVG(selling_price) as avg_price
                FROM channel_listings
                GROUP BY marketplace
                ORDER BY listing_count DESC
            """
            df = pd.read_sql(text(query), self.engine)
            logger.info(f"Found {len(df)} marketplaces")
            return df
        except Exception as e:
            logger.error(f"Error fetching marketplace listings: {str(e)}", exc_info=True)
            raise DatabaseException(f"Marketplace listings fetch failed: {str(e)}") from e

    def get_profitability_summary(self):
        """
        Get high-level profitability KPIs across all marketplaces.

        Returns:
            dict: {avg_margin, total_net_profit, profitable_skus, loss_skus}
        """
        try:
            logger.debug("Fetching profitability summary...")
            # Lazy import to avoid circular dependency
            from src.core.services.finance_service import FinanceService
            finance = FinanceService()
            df = finance.calculate_profitability()

            if df is None or df.empty:
                logger.warning("No profitability data available")
                return {
                    "avg_margin": 0.0,
                    "total_net_profit": 0.0,
                    "profitable_skus": 0,
                    "loss_skus": 0,
                }

            summary = {
                "avg_margin": float(df['margin_pct'].mean()),
                "total_net_profit": float(df['net_profit'].sum()),
                "profitable_skus": int(len(df[df['net_profit'] > 0])),
                "loss_skus": int(len(df[df['net_profit'] <= 0])),
            }
            logger.info(f"Profitability summary: {summary}")
            return summary
        except Exception as e:
            logger.error(f"Error fetching profitability summary: {str(e)}", exc_info=True)
            raise ServiceException(f"Profitability summary failed: {str(e)}") from e

    def get_gap_summary(self):
        """
        Get catalog gap/coverage summary.

        Returns:
            dict: {total_opportunities, missing_listings, coverage_pct}
        """
        try:
            logger.debug("Fetching gap summary...")
            from src.core.services.gap_service import GapService
            gap = GapService()
            df = gap.get_gap_matrix()

            if df is None or df.empty:
                return {"total_opportunities": 0, "missing_listings": 0, "coverage_pct": 0.0}

            total = len(df)
            missing = int(len(df[df['listing_count'] == 0]))
            coverage = (100 - (missing / total * 100)) if total > 0 else 0.0

            summary = {
                "total_opportunities": total,
                "missing_listings": missing,
                "coverage_pct": round(coverage, 1),
            }
            logger.info(f"Gap summary: {summary}")
            return summary
        except Exception as e:
            logger.error(f"Error fetching gap summary: {str(e)}", exc_info=True)
            raise ServiceException(f"Gap summary failed: {str(e)}") from e

    def get_inventory_summary(self):
        """
        Get inventory health summary.

        Returns:
            dict: {total_skus_tracked, out_of_stock, low_stock}
        """
        try:
            logger.debug("Fetching inventory summary...")
            with self.engine.connect() as conn:
                tracked = conn.execute(
                    text("SELECT COUNT(*) FROM inventory_master")
                ).scalar() or 0

                out_of_stock = conn.execute(
                    text("SELECT COUNT(*) FROM inventory_master WHERE godown_stock_packs <= 0")
                ).scalar() or 0

                # Low stock now uses the per-SKU reorder_point (Roadmap 1.3.3).
                # Only SKUs with a configured reorder_point (> 0) are evaluated;
                # out-of-stock SKUs are excluded so the two metrics don't overlap.
                low_stock = conn.execute(
                    text(
                        "SELECT COUNT(*) FROM inventory_master "
                        "WHERE reorder_point > 0 "
                        "AND godown_stock_packs > 0 "
                        "AND godown_stock_packs <= reorder_point"
                    )
                ).scalar() or 0

            summary = {
                "total_skus_tracked": tracked,
                "out_of_stock": out_of_stock,
                "low_stock": low_stock,
            }
            logger.info(f"Inventory summary: {summary}")
            return summary
        except Exception as e:
            logger.error(f"Error fetching inventory summary: {str(e)}", exc_info=True)
            # Inventory tables may not exist on fresh DB — return safe defaults
            return {"total_skus_tracked": 0, "out_of_stock": 0, "low_stock": 0}

    def get_top_profitable_skus(self, limit=10):
        """
        Get top N profitable SKUs across marketplaces.

        Returns:
            DataFrame: top N rows by net_profit
        """
        try:
            from src.core.services.finance_service import FinanceService
            finance = FinanceService()
            df = finance.calculate_profitability()

            if df is None or df.empty:
                return pd.DataFrame()

            return df.nlargest(limit, 'net_profit')
        except Exception as e:
            logger.error(f"Error fetching top SKUs: {str(e)}", exc_info=True)
            return pd.DataFrame()