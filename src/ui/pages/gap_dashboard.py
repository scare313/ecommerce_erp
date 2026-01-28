import streamlit as st
import pandas as pd
from src.core.services.gap_service import GapService

def render():
    st.title("🕳️ Gap Analysis Dashboard")
    st.markdown("Shows the **Number of Listings** per Pack on each Marketplace.")

    service = GapService()
    try:
        df = service.get_gap_matrix()
    except Exception as e:
        st.error(f"Error loading data: {e}")
        return

    if df.empty:
        st.warning("No data found. Please upload Packs and Listings first.")
        return

    # --- TOP METRICS ---
    total_opps = len(df)
    # Count rows where listing_count is 0
    missing_count = len(df[df['listing_count'] == 0])
    coverage = 100 - (missing_count / total_opps * 100) if total_opps > 0 else 0

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

    # --- PIVOT TABLE ---
    # Values are now Integers (Counts), so no duplicates possible
    pivot_table = df.pivot(index='pack_sku', columns='marketplace', values='listing_count')

    # 1. Apply Search
    if search_query:
        pivot_table = pivot_table[pivot_table.index.str.contains(search_query, case=False)]

    # 2. Apply "Gaps Only" Filter
    if show_gaps_only:
        # Keep rows where ANY column is 0
        mask = (pivot_table == 0).any(axis=1)
        pivot_table = pivot_table[mask]

    # --- VISUALIZATION ---
    def color_cells(val):
        if val == 0:
            return 'background-color: #ffe6e6; color: #cc0000; font-weight: bold;' # Red
        else:
            return 'background-color: #e6fffa; color: #006600;' # Green

    st.write(f"Showing **{len(pivot_table)}** Packs.")
    
    st.dataframe(
        pivot_table.style.map(color_cells),
        use_container_width=True,
        height=700
    )