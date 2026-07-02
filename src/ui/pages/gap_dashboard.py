"""Gap Analysis Dashboard.

Shows coverage opportunities and missing listings by marketplace:
- Catalog coverage metrics (goal: 100%)
- Gap matrix showing pack-marketplace combinations
- Highlights missing listings in red
- Searchable and filterable by pack SKU
"""
import streamlit as st
import pandas as pd
from src.core.services.gap_service import GapService
from src.infrastructure.logger import get_logger
from src.ui.components.errors import show_error
from src.core.cache import get_gap_matrix_cached

logger = get_logger(__name__)

def render():
    """Render the Gap Analysis Dashboard page."""
    try:
        logger.info("Rendering Gap Analysis Dashboard...")
        st.title("🕳️ Gap Analysis Dashboard")
        st.markdown("Shows the **Number of Listings** per Pack on each Marketplace.")

        try:
            service = GapService()
            logger.debug("GapService initialized")
            df = get_gap_matrix_cached()
            logger.info(f"Gap matrix retrieved: {len(df)} rows")
        except Exception as e:
            show_error(logger, "Error loading gap analysis data", e)
            return

        if df.empty:
            logger.warning("No data found for gap analysis")
            st.warning("No data found. Please upload Packs and Listings first.")
            return

        # --- TOP METRICS ---
        total_opps = len(df)
        # Count rows where listing_count is 0
        missing_count = len(df[df['listing_count'] == 0])
        coverage = 100 - (missing_count / total_opps * 100) if total_opps > 0 else 0

        logger.info(f"Gap Analysis Summary: Total={total_opps}, Missing={missing_count}, Coverage={coverage:.1f}%")

        c1, c2, c3 = st.columns(3)
        c1.metric("Total Opportunities", total_opps, help="Total Packs × Total Marketplaces")
        c2.metric("Missing Listings", missing_count, delta=-missing_count, delta_color="inverse")
        c3.metric("Catalog Coverage", f"{coverage:.1f}%", help="Goal is 100%")

        st.divider()

        # --- CONTROLS ---
        col_filter, col_search = st.columns([1, 2])
        with col_filter:
            show_gaps_only = st.checkbox("Show only Missing (0)", value=True)
        with col_search:
            search_query = st.text_input("Search SKU", placeholder="Type to filter...")

        logger.debug(f"Filters applied: gaps_only={show_gaps_only}, search={search_query}")

                # --- PIVOT TABLE ---
        st.subheader("📊 Listing Coverage Matrix")

        # Build pivot: rows=pack_sku, cols=marketplace, values=listing_count (0 or 1)
        pivot_table = df.pivot_table(
            index='pack_sku',
            columns='marketplace',
            values='listing_count',
            fill_value=0
        )

        # Apply "Show only Missing" filter
        if show_gaps_only:
            mask = (pivot_table == 0).any(axis=1)
            pivot_table = pivot_table[mask]
            logger.debug(f"Filtered to {len(pivot_table)} packs with gaps")

        # Apply search filter
        if search_query:
            pivot_table = pivot_table[
                pivot_table.index.str.contains(search_query, case=False, na=False)
            ]
            logger.debug(f"After search filter: {len(pivot_table)} packs")

        if pivot_table.empty:
            st.info("No packs match your filters.")
            return

        # Color-code: 0 = red (missing), 1 = green (listed)
        def color_cells(val):
            if val == 0:
                return 'background-color: #ffcccc; color: #cc0000;'
            else:
                return 'background-color: #ccffcc; color: #006600;'

        st.dataframe(
            pivot_table.style.map(color_cells),
            width='stretch',
            height=600
        )

        st.caption(f"Showing {len(pivot_table)} packs × {len(pivot_table.columns)} marketplaces")
    except Exception as e:
        show_error(logger, "Critical error in Gap Analysis Dashboard", e)