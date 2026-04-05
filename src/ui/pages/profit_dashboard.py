import streamlit as st
import plotly.express as px
from src.core.services.finance_service import FinanceService

def render():
    st.title("💰 SKU Profitability")
    
    service = FinanceService()
    mkt = st.selectbox("Select Marketplace", ["Amazon", "Flipkart", "Meesho"], index=0)
    
    df = service.calculate_profitability(mkt)
    
    if df.empty:
        st.warning("No listings found. Please add listings in Data Manager.")
        return

    # Calculate how many SKUs are missing prices
    df_with_price = df[df['selling_price'] > 0]
    df_without_price = df[df['selling_price'] <= 0]
    
    if len(df_without_price) > 0:
        st.warning(f"⚠️ {len(df_without_price)} listing(s) missing selling price. Profit calculations will be inaccurate. Please set prices in Data Manager → Listings tab.")
    
    if df_with_price.empty:
        st.error("❌ No pricing data found. Please update 'Selling Price' in Data Manager.")
        return

    # Filter & Sort
    df = df_with_price.sort_values('margin_pct')

    # Metrics
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Avg Margin", f"{df['margin_pct'].mean():.1f}%")
    c2.metric("Avg Net Profit", f"₹{df['net_profit'].mean():.1f}")
    c3.metric("Profitable SKUs", len(df[df['net_profit'] > 0]))
    c4.metric("Loss Makers", len(df[df['net_profit'] <= 0]), delta_color="inverse")

    st.divider()

    # --- CLEAN TABLE ---
    st.subheader("Profit Breakdown")
    
    # Selecting ONLY useful columns
    view_df = df[[
        'channel_sku', 'product_name', 
        'selling_price', 'total_cogs', 
        'total_platform_fees', 'net_profit', 'margin_pct'
    ]].copy()

    # Renaming for display
    view_df.columns = ['SKU', 'Product Name', 'Price', 'COGS', 'Fees+GST', 'Net Profit', 'Margin %']

    # Color logic for Margin column
    st.dataframe(
        view_df.style.background_gradient(subset=['Margin %'], cmap="RdYlGn", vmin=-10, vmax=30)
               .format({
                   "Price": "₹{:.0f}", 
                   "COGS": "₹{:.0f}", 
                   "Fees+GST": "₹{:.0f}", 
                   "Net Profit": "₹{:.0f}", 
                   "Margin %": "{:.1f}%"
               }),
        width='stretch',
        height=600,
        hide_index=True
    )