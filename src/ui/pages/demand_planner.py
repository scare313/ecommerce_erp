import streamlit as st
import pandas as pd
from src.core.services.inventory_service import InventoryService
from datetime import datetime
import io

def render():
    st.title("📉 Market Demand Planner")
    st.markdown("""
    Upload your sales reports to generate a **Purchase Order Plan**.
    This uses your **Product Master** & **Pack Master** data to calculate exactly how many base units you need to buy from suppliers.
    """)

    service = InventoryService()

    # --- SIDEBAR CONFIG ---
    st.sidebar.header("Planning Parameters")
    sales_days = st.sidebar.number_input("Sales History (Days)", value=30, help="Duration of the uploaded sales reports")
    purchase_period = st.sidebar.number_input("Days to Cover", value=15, help="How many days of stock do you want to buy?")
    lead_time = st.sidebar.number_input("Supplier Lead Time", value=10, help="Days it takes for stock to arrive")
    safety_stock = st.sidebar.number_input("Safety Stock (Days)", value=7, help="Buffer for unexpected demand")

    # --- FILE UPLOADS ---
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        f_amazon = st.file_uploader("Amazon Sales (CSV)", type="csv")
    with c2:
        f_flipkart = st.file_uploader("Flipkart Orders", type=["csv", "xlsx"])
    with c3:
        f_meesho = st.file_uploader("Meesho Orders", type=["csv", "xlsx"])
    with c4:
        f_stock = st.file_uploader("Current Stock (Base SKU)", type=["csv", "xlsx"], help="Columns: SKU, Qty")

    # --- ACTION ---
    if st.button("🚀 Generate Purchase Plan", type="primary"):
        if not (f_amazon or f_flipkart or f_meesho):
            st.error("Please upload at least one sales report.")
            return

        with st.spinner("Crunching numbers..."):
            files = {
                'amazon': f_amazon,
                'flipkart': f_flipkart,
                'meesho': f_meesho,
                'stock': f_stock
            }
            params = {
                'sales_days': sales_days,
                'purchase_period': purchase_period,
                'lead_time': lead_time,
                'safety_stock': safety_stock
            }
            
            plan_df, orphans_df = service.generate_purchase_plan(files, params)

            if plan_df is None:
                st.error(orphans_df) # Error message in this case
                return

            # --- RESULTS ---
            st.success(f"Plan Generated! Found demand for {len(plan_df)} Unique Base Products.")
            
            # Key Metrics
            total_buy_units = plan_df['to_purchase'].sum()
            # If cost price exists in DB, we could calc value. For now, just units.
            
            m1, m2 = st.columns(2)
            m1.metric("Total Units to Order", f"{total_buy_units:,.0f}")
            m2.metric("SKUs to Restock", len(plan_df[plan_df['to_purchase'] > 0]))

            # Tabs
            tab1, tab2 = st.tabs(["📋 Purchase Plan", "⚠️ Unmapped Sales"])

            with tab1:
                # Color code high quantity
                st.dataframe(
                    plan_df.style.background_gradient(subset=['to_purchase'], cmap="Greens"),
                    width='stretch',
                    height=600
                )
                
                # Download
                buffer = io.BytesIO()
                with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                    plan_df.to_excel(writer, sheet_name="Purchase Plan", index=False)
                    if not orphans_df.empty:
                        orphans_df.to_excel(writer, sheet_name="Unmapped SKUs", index=False)
                
                st.download_button(
                    label="⬇️ Download Plan (.xlsx)",
                    data=buffer.getvalue(),
                    file_name=f"Purchase_Plan_{datetime.now().strftime('%Y-%m-%d')}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )

            with tab2:
                if not orphans_df.empty:
                    st.warning("These SKUs were found in Sales Reports but NOT in your Database. Please add them in Data Manager.")
                    st.dataframe(orphans_df)
                else:
                    st.info("All sales mapped successfully to database!")