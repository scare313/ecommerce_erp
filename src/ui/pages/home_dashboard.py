"""Home / Executive KPI Dashboard.

Provides a unified executive overview of the entire ERP system:
- Catalog health (products, packs, listings, marketplaces)
- Profitability metrics (avg margin, net profit, profitable vs loss SKUs)
- Gap analysis coverage (missing listings, coverage %)
- Inventory status (tracked SKUs, out-of-stock, low-stock)
- Top performing SKUs and marketplace distribution charts
"""
import streamlit as st
import pandas as pd
import plotly.express as px
from src.core.services.dashboard_service import DashboardService
from src.infrastructure.logger import get_logger
from src.ui.components.errors import show_error

logger = get_logger(__name__)


def render():
    """Render the Home / KPI Dashboard page."""
    try:
        logger.info("Rendering Home/KPI Dashboard...")
        st.title("🏠 Home — Executive KPI Dashboard")
        st.markdown(
            "A unified snapshot of your **catalog, profitability, gaps, and inventory** — "
            "all in one place."
        )

        # ---------- Initialize Service ----------
        try:
            service = DashboardService()
            logger.debug("DashboardService initialized")
        except Exception as e:
            show_error(logger, "Failed to initialize dashboard service", e)
            return

        # ============================================================
        # ROW 1 — CATALOG OVERVIEW
        # ============================================================
        st.subheader("📦 Catalog Overview")
        try:
            cat = service.get_catalog_kpis()
            c1, c2, c3, c4, c5 = st.columns(5)
            c1.metric("Products", f"{cat['total_products']:,}")
            c2.metric("Packs", f"{cat['total_packs']:,}")
            c3.metric("Listings", f"{cat['total_listings']:,}")
            c4.metric("Active Listings", f"{cat['active_listings']:,}")
            c5.metric("Marketplaces", f"{cat['marketplaces']:,}")
            logger.debug(f"Catalog KPIs rendered: {cat}")
        except Exception as e:
            logger.error(f"Error rendering catalog KPIs: {str(e)}", exc_info=True)
            st.warning(f"Could not load catalog KPIs: {e}")

        st.divider()

        # ============================================================
        # ROW 2 — PROFITABILITY + GAP COVERAGE
        # ============================================================
        col_left, col_right = st.columns(2)

        # ---------- Profitability ----------
        with col_left:
            st.subheader("💰 Profitability Snapshot")
            try:
                prof = service.get_profitability_summary()
                p1, p2 = st.columns(2)
                p1.metric("Avg Margin", f"{prof['avg_margin']:.1f}%")
                p2.metric("Total Net Profit", f"₹{prof['total_net_profit']:,.0f}")

                p3, p4 = st.columns(2)
                p3.metric("Profitable SKUs", f"{prof['profitable_skus']:,}")
                p4.metric("Loss Makers", f"{prof['loss_skus']:,}", delta_color="inverse")
                logger.debug(f"Profitability summary rendered: {prof}")
            except Exception as e:
                logger.error(f"Error rendering profitability: {str(e)}", exc_info=True)
                st.warning(f"Profitability data unavailable: {e}")

        # ---------- Gap Coverage ----------
        with col_right:
            st.subheader("🕳️ Catalog Coverage")
            try:
                gap = service.get_gap_summary()
                g1, g2, g3 = st.columns(3)
                g1.metric("Total Opportunities", f"{gap['total_opportunities']:,}")
                g2.metric("Missing Listings", f"{gap['missing_listings']:,}",
                          delta_color="inverse")
                g3.metric("Coverage", f"{gap['coverage_pct']:.1f}%")

                # Progress bar visualisation
                st.progress(min(gap['coverage_pct'] / 100, 1.0))
                logger.debug(f"Gap summary rendered: {gap}")
            except Exception as e:
                logger.error(f"Error rendering gap summary: {str(e)}", exc_info=True)
                st.warning(f"Gap data unavailable: {e}")

        st.divider()

        # ============================================================
        # ROW 3 — INVENTORY HEALTH
        # ============================================================
        st.subheader("📊 Inventory Health")
        try:
            inv = service.get_inventory_summary()
            i1, i2, i3 = st.columns(3)
            i1.metric("SKUs Tracked", f"{inv['total_skus_tracked']:,}")
            i2.metric("Out of Stock", f"{inv['out_of_stock']:,}", delta_color="inverse")
            i3.metric("Low Stock (<5)", f"{inv['low_stock']:,}", delta_color="inverse")
            logger.debug(f"Inventory summary rendered: {inv}")
        except Exception as e:
            logger.error(f"Error rendering inventory summary: {str(e)}", exc_info=True)
            st.warning(f"Inventory data unavailable: {e}")

        st.divider()

        # ============================================================
        # ROW 4 — VISUALIZATIONS
        # ============================================================
        st.subheader("📈 Marketplace Distribution & Top Performers")

        viz_left, viz_right = st.columns(2)

        # ---------- Listings by Marketplace ----------
        with viz_left:
            st.markdown("**Listings by Marketplace**")
            try:
                df_mkt = service.get_listings_by_marketplace()
                if df_mkt is not None and not df_mkt.empty:
                    fig = px.bar(
                        df_mkt,
                        x="marketplace",
                        y="listing_count",
                        text="listing_count",
                        color="marketplace",
                        title=None,
                    )
                    fig.update_traces(textposition="outside")
                    fig.update_layout(showlegend=False, height=350,
                                      margin=dict(l=10, r=10, t=20, b=10))
                    st.plotly_chart(fig, width='stretch')
                else:
                    st.info("No marketplace listing data available.")
            except Exception as e:
                logger.error(f"Error rendering marketplace chart: {str(e)}", exc_info=True)
                st.warning(f"Marketplace chart unavailable: {e}")

        # ---------- Top Profitable SKUs ----------
        with viz_right:
            st.markdown("**Top 10 Profitable SKUs**")
            try:
                top_df = service.get_top_profitable_skus(limit=10)
                if top_df is not None and not top_df.empty:
                    # Pick a sensible subset of columns if they exist
                    display_cols = [c for c in
                                    ['channel_sku', 'marketplace', 'net_profit', 'margin_pct']
                                    if c in top_df.columns]
                    show_df = top_df[display_cols].copy()

                    # Rename for nicer display
                    rename_map = {
                        'channel_sku': 'SKU',
                        'marketplace': 'Marketplace',
                        'net_profit': 'Net Profit',
                        'margin_pct': 'Margin %',
                    }
                    show_df = show_df.rename(columns=rename_map)

                    st.dataframe(
                        show_df.style.background_gradient(
                            subset=[c for c in ['Net Profit', 'Margin %'] if c in show_df.columns],
                            cmap="RdYlGn"
                        ).format({
                            "Net Profit": "₹{:.0f}",
                            "Margin %": "{:.1f}%",
                        }),
                        width='stretch',
                        height=350,
                        hide_index=True,
                    )
                else:
                    st.info("No profitability data available yet.")
            except Exception as e:
                logger.error(f"Error rendering top SKUs: {str(e)}", exc_info=True)
                st.warning(f"Top SKUs unavailable: {e}")

        st.divider()

        # ============================================================
        # ROW 5 — QUICK NAVIGATION HINTS
        # ============================================================
        st.subheader("🚀 Quick Actions")
        q1, q2, q3, q4 = st.columns(4)
        q1.info("💰 **Profit Dashboard**\nDeep-dive into SKU margins")
        q2.info("🕳️ **Gap Analysis**\nFix missing marketplace listings")
        q3.info("📉 **Demand Planner**\nGenerate purchase orders")
        q4.info("📦 **Inventory Manager**\nUpdate godown stock")

        st.caption("Use the sidebar to navigate to any dashboard.")

    except Exception as e:
        show_error(logger, "Critical error rendering Home Dashboard", e)