"""SKU Profitability Dashboard.

Displays profit metrics and margin analysis by marketplace:
- Profitability trends and key metrics
- Profit breakdown table with color-coded margins
- Identifies loss-making and profitable SKUs
- Alerts for missing pricing data
"""
import streamlit as st
import plotly.express as px
from src.core.services.finance_service import FinanceService
from src.infrastructure.logger import get_logger

logger = get_logger(__name__)

def render():
    """Render the Profit Dashboard page."""
    try:
        logger.info("Rendering Profit Dashboard...")
        st.title("💰 SKU Profitability")
        
        try:
            service = FinanceService()
            logger.debug("FinanceService initialized")
        except Exception as e:
            logger.error(f"Failed to initialize FinanceService: {str(e)}", exc_info=True)
            st.error(f"Failed to initialize service: {str(e)}")
            return
        
        try:
            mkt = st.selectbox("Select Marketplace", ["Amazon", "Flipkart", "Meesho"], index=0)
            logger.debug(f"User selected marketplace: {mkt}")
            df = service.calculate_profitability(mkt)
        except Exception as e:
            logger.error(f"Error calculating profitability for {mkt}: {str(e)}", exc_info=True)
            st.error(f"Error calculating profitability: {str(e)}")
            return
        
        if df.empty:
            logger.warning(f"No listings found for marketplace: {mkt}")
            st.warning("No listings found. Please add listings in Data Manager.")
            return

        # Calculate how many SKUs are missing prices
        df_with_price = df[df['selling_price'] > 0]
        df_without_price = df[df['selling_price'] <= 0]
        
        if len(df_without_price) > 0:
            logger.warning(f"{len(df_without_price)} listings missing prices on {mkt}")
            st.warning(f"⚠️ {len(df_without_price)} listing(s) missing selling price. Profit calculations will be inaccurate. Please set prices in Data Manager → Listings tab.")
        
        if df_with_price.empty:
            logger.warning(f"No pricing data found for {mkt}")
            st.error("❌ No pricing data found. Please update 'Selling Price' in Data Manager.")
            return

        # Filter & Sort
        df = df_with_price.sort_values('margin_pct')
        logger.info(f"Profitability calculated for {len(df)} listings on {mkt}")

        # Metrics
        c1, c2, c3, c4 = st.columns(4)
        avg_margin = df['margin_pct'].mean()
        avg_profit = df['net_profit'].mean()
        profitable_count = len(df[df['net_profit'] > 0])
        loss_count = len(df[df['net_profit'] <= 0])
        
        c1.metric("Avg Margin", f"{avg_margin:.1f}%")
        c2.metric("Avg Net Profit", f"₹{avg_profit:.1f}")
        c3.metric("Profitable SKUs", profitable_count)
        c4.metric("Loss Makers", loss_count, delta_color="inverse")
        
        logger.debug(f"Dashboard metrics - Avg Margin: {avg_margin:.1f}%, Profitable: {profitable_count}, Loss: {loss_count}")

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
    except Exception as e:
        logger.error(f"Critical error in Profit Dashboard render: {str(e)}", exc_info=True)
        st.error(f"Critical error: {e}")