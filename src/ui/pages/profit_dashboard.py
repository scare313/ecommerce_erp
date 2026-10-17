"""SKU Profitability Dashboard."""
import streamlit as st
import plotly.express as px
from src.core.services.finance_service import FinanceService
from src.infrastructure.logger import get_logger
from src.ui.components.errors import show_error
from src.ui.cache_adapter import get_profitability_cached
from src.ui.cache_adapter import get_marketplaces
from src.ui.cache_adapter import get_seller_accounts

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
            show_error(logger, "Failed to initialize finance service", e)
            return

        # --- Marketplace selector ---
        marketplaces = get_marketplaces()
        if not marketplaces:
            st.error("No marketplaces configured. Add them in Catalog → Config.")
            return
        accounts = get_seller_accounts()
        sel_col1, sel_col2 = st.columns(2)
        with sel_col1:
            if len(accounts) > 1:
                acct_labels = ["All accounts"] + [a[1] for a in accounts]
                acct_choice = st.selectbox(
                    "Seller Account", acct_labels, index=0, key="profit_acct"
                )
                acct = None
                if acct_choice != "All accounts":
                    acct = next(a[0] for a in accounts if a[1] == acct_choice)
            else:
                acct = None
                st.selectbox(
                    "Seller Account",
                    [accounts[0][1] if accounts else "Account 1"],
                    disabled=True,
                    key="profit_acct",
                )
        with sel_col2:
            mkt = st.selectbox("Marketplace", marketplaces, index=0, key="profit_mkt")
        logger.debug(f"User selected marketplace: {mkt}, account: {acct or 'ALL'}")

        # --- Fee rules freshness indicator ---
        try:
            from src.core.data_access import get_rules_last_verified
            from datetime import datetime, timezone
            lv_str = get_rules_last_verified()
            if lv_str:
                lv = datetime.fromisoformat(lv_str)
                days_ago = (datetime.now(timezone.utc).replace(tzinfo=None) - lv).days
                if days_ago > 30:
                    st.warning(
                        f"⚠️ Fee rules were last verified **{days_ago} days ago** "
                        f"({lv.strftime('%d %b %Y')}). "
                        "Margins may be stale — check **Config & Rules** in Catalog."
                    )
                else:
                    st.caption(f"Fee rules verified {lv.strftime('%d %b %Y')}")
        except Exception:
            pass

        # --- Fetch profitability data ---
        try:
            df = get_profitability_cached(marketplace=mkt, account=acct)
        except Exception as e:
            show_error(logger, "Failed to calculate profitability", e)
            return

        if df is None or df.empty:
            st.warning("No data available for the selected marketplace.")
            return

        # --- Warn about missing prices ---
        missing_prices = df[df['selling_price'].isna() | (df['selling_price'] <= 0)]
        if len(missing_prices) > 0:
            logger.warning(f"{len(missing_prices)} listings missing prices on {mkt}")
            st.warning(f"⚠️ {len(missing_prices)} listings have no selling price set. Update them in Catalog.")
            df = df[df['selling_price'] > 0]

        if df.empty:
            st.info("No listings with valid prices to display.")
            return

        # --- Metrics ---
        c1, c2, c3, c4, c5 = st.columns(5)
        avg_margin = df['margin_pct'].mean()
        avg_profit = df['net_profit'].mean()
        profitable_count = len(df[df['net_profit'] > 0])
        loss_count = len(df[df['net_profit'] <= 0])
        avg_itc = df['gst_on_fees'].mean() if 'gst_on_fees' in df.columns else 0.0

        c1.metric("Avg Margin", f"{avg_margin:.1f}%",
                  help="Net profit as a share of revenue excluding GST")
        c2.metric("Avg Net Profit", f"₹{avg_profit:.1f}", help="Per unit sold")
        c3.metric("Profitable SKUs", profitable_count)
        c4.metric("Loss Makers", loss_count, delta_color="inverse")
        c5.metric("Avg ITC per unit", f"₹{avg_itc:.2f}",
                  help="GST paid on marketplace fees — reclaimed in your GST "
                       "return, so it is not counted as a cost here")

        logger.debug(
            f"Dashboard metrics - Avg Margin: {avg_margin:.1f}%, "
            f"Profitable: {profitable_count}, Loss: {loss_count}"
        )

        st.divider()

        # --- Profit breakdown table ---
        st.subheader("Profit Breakdown")

        st.caption(
            "Revenue excludes output GST. Fees exclude the GST you reclaim as "
            "input credit. TCS and TDS are withheld from your payout but "
            "recovered later, so they are not charged against profit."
        )

        cols = [
            'channel_sku', 'product_name', 'selling_price', 'revenue_ex_gst',
            'total_cogs', 'shipping_fee', 'fees_ex_gst', 'net_profit', 'margin_pct',
        ]
        labels = [
            'Listing SKU', 'Product Name', 'Price (incl GST)', 'Revenue (ex GST)',
            'COGS', 'of which Shipping', 'Fees (ex GST)', 'Net Profit', 'Margin %',
        ]
        cols = [c for c in cols if c in df.columns]
        labels = labels[:len(cols)]
        view_df = df[cols].copy()
        view_df.columns = labels

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
                c: '₹{:.2f}' for c in view_df.columns
                if c not in ('Listing SKU', 'Product Name', 'Margin %')
            } | {'Margin %': '{:.1f}%'}),
            width='stretch',
            hide_index=True,
            height=500
        )

        logger.info(f"Displayed profitability for {len(view_df)} SKUs")

    except Exception as e:
        show_error(logger, "Error in Profit Dashboard", e)