"""Profit page.

Two tabs answering two different questions:
    Monthly P&L  — what did we actually make? From marketplace settlement files.
    Price check  — at today's price, what should one unit earn? From fee rules.
"""
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
    """Render the Profit page: Monthly P&L (actual) and Price check (planned)."""
    st.title("💰 Profit")
    tab_actual, tab_planned = st.tabs(["📅 Monthly P&L", "🧮 Price check"])
    with tab_actual:
        from src.ui.pages import monthly_pnl
        monthly_pnl.render()
    with tab_planned:
        _render_price_check()


def _render_price_check():
    """Per-listing unit economics at the current price (planned, not actual)."""
    try:
        logger.info("Rendering Profit Dashboard...")
        st.caption(
            "What one unit **should** earn at today's listing price, using the fee "
            "rules in Catalog → Config & Rules. For what you actually earned, use "
            "**Monthly P&L**."
        )

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

        # Column -> label, kept as pairs so a missing column can never shift labels.
        columns = {
            'channel_sku': 'Listing SKU',
            'account_id': 'Account',
            'selling_price': 'Price (incl GST)',
            'revenue_ex_gst': 'Revenue (ex GST)',
            'total_cogs': 'Goods + packaging',
            'shipping_fee': 'Shipping',
            'fees_ex_gst': 'All fees incl. shipping (ex GST)',
            'net_profit': 'Net Profit',
            'margin_pct': 'Margin %',
        }
        columns = {c: label for c, label in columns.items() if c in df.columns}
        view_df = df[list(columns)].rename(columns=columns)

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
                if c not in ('Listing SKU', 'Account', 'Margin %')
            } | {'Margin %': '{:.1f}%'}),
            width='stretch',
            hide_index=True,
            height=500
        )

        logger.info(f"Displayed profitability for {len(view_df)} SKUs")

    except Exception as e:
        show_error(logger, "Error in Profit Dashboard", e)