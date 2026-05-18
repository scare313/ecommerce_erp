"""SKU Profitability Dashboard."""
import streamlit as st
import plotly.express as px
from src.core.services.finance_service import FinanceService
from src.infrastructure.logger import get_logger
from src.core.cache import get_profitability_cached
from src.core.cache import get_marketplaces

logger = get_logger(__name__)


def render():
    """Render the Profit Dashboard page."""
    try:
        logger.info("Rendering Profit Dashboard...")
        st.title("💰 SKU Profitability")

        # --- Init service ---
        try:
            service = FinanceService()
            logger.debug("FinanceService initialized")
        except Exception as e:
            logger.error(f"Failed to initialize FinanceService: {str(e)}", exc_info=True)
            st.error(f"Failed to initialize service: {str(e)}")
            return

        # --- Marketplace selector ---
        marketplaces = get_marketplaces()
        if not marketplaces:
            st.error("No marketplaces configured. Add them in Data Manager → Config.")
            return
        mkt = st.selectbox("Select Marketplace", marketplaces, index=0, key="profit_mkt")
        logger.debug(f"User selected marketplace: {mkt}")

        # --- Fetch profitability data ---
        try:
            df = get_profitability_cached(marketplace=mkt)
        except Exception as e:
            logger.error(f"Failed to calculate profitability: {str(e)}", exc_info=True)
            st.error(f"Failed to load profitability data: {str(e)}")
            return

        if df is None or df.empty:
            st.warning("No data available for the selected marketplace.")
            return

        # --- Warn about missing prices ---
        missing_prices = df[df['selling_price'].isna() | (df['selling_price'] <= 0)]
        if len(missing_prices) > 0:
            logger.warning(f"{len(missing_prices)} listings missing prices on {mkt}")
            st.warning(f"⚠️ {len(missing_prices)} listings have no selling price set. Update them in Data Manager.")
            df = df[df['selling_price'] > 0]

        if df.empty:
            st.info("No listings with valid prices to display.")
            return

        # --- Metrics ---
        c1, c2, c3, c4 = st.columns(4)
        avg_margin = df['margin_pct'].mean()
        avg_profit = df['net_profit'].mean()
        profitable_count = len(df[df['net_profit'] > 0])
        loss_count = len(df[df['net_profit'] <= 0])

        c1.metric("Avg Margin", f"{avg_margin:.1f}%")
        c2.metric("Avg Net Profit", f"₹{avg_profit:.1f}")
        c3.metric("Profitable SKUs", profitable_count)
        c4.metric("Loss Makers", loss_count, delta_color="inverse")

        logger.debug(
            f"Dashboard metrics - Avg Margin: {avg_margin:.1f}%, "
            f"Profitable: {profitable_count}, Loss: {loss_count}"
        )

        st.divider()

        # --- Profit breakdown table ---
        st.subheader("Profit Breakdown")

        view_df = df[[
            'channel_sku', 'product_name', 'selling_price', 'total_cogs',
            'total_platform_fees', 'net_profit', 'margin_pct'
        ]].copy()
        view_df.columns = ['SKU', 'Product Name', 'Price', 'COGS', 'Fees+GST', 'Net Profit', 'Margin %']

        # Color-code margin column
        def highlight_margin(val):
            if val < 0:
                return 'background-color: #ffcccc'
            elif val < 10:
                return 'background-color: #fff4cc'
            else:
                return 'background-color: #ccffcc'

        st.dataframe(
            view_df.style.map(highlight_margin, subset=['Margin %']).format({
                'Price': '₹{:.2f}',
                'COGS': '₹{:.2f}',
                'Fees+GST': '₹{:.2f}',
                'Net Profit': '₹{:.2f}',
                'Margin %': '{:.1f}%',
            }),
            width='stretch',
            hide_index=True,
            height=500
        )

        logger.info(f"Displayed profitability for {len(view_df)} SKUs")

    except Exception as e:
        logger.error(f"Error in Profit Dashboard render: {str(e)}", exc_info=True)
        st.error(f"❌ An unexpected error occurred: {str(e)}")