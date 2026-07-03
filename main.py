"""ERP Application Entry Point.

Initializes database and renders Streamlit UI with navigation to multiple dashboards.
"""
import sys
from pathlib import Path

import streamlit as st

project_root = Path(__file__).parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.infrastructure.init_db import init_database
from src.infrastructure.logger import get_logger
from src.ui.components.errors import show_error

logger = get_logger(__name__)

try:
    logger.info("Starting ERP Application...")
    
    if not st.session_state.get("db_initialized"):
        try:
            success = init_database()
            if success:
                st.session_state.db_initialized = True
                logger.info("✅ Database initialized successfully")
            else:
                logger.error("Database initialization returned False")
                st.error("❌ Failed to initialize database. Please check logs for details.")
                st.stop()
        except Exception as e:
            show_error(logger, "Failed to initialize the database", e)
            st.stop()
    
    st.set_page_config(page_title="Ecommerce ERP", layout="wide")

    st.sidebar.title("Navigation")

    # Grouped button nav — persists active page across reruns via session state.
    # Buttons are grouped by workflow persona; the active page gets type="primary"
    # (green fill) for a clear visual indicator.
    if "nav_page" not in st.session_state:
        st.session_state.nav_page = "Home"
    page = st.session_state.nav_page

    def _nav(label: str, name: str) -> None:
        """Render one sidebar nav button; clicking sets the active page."""
        if st.sidebar.button(
            label,
            use_container_width=True,
            type="primary" if page == name else "secondary",
            key=f"nav_btn_{name}",
        ):
            st.session_state.nav_page = name

    _nav("🏠 Home", "Home")

    st.sidebar.caption("OPERATE")
    _nav("📦 Inventory Manager", "Inventory Manager")
    _nav("🏭 Supplier Master",   "Supplier Master")

    st.sidebar.caption("CATALOG & SELL")
    _nav("🗂️ Catalog",      "Catalog")
    _nav("🔭 Gap Analysis", "Gap Analysis")

    st.sidebar.caption("ANALYZE")
    _nav("💰 Profit Dashboard", "Profit Dashboard")
    _nav("📈 Demand Planner",   "Demand Planner")

    st.sidebar.caption("SETUP")
    _nav("🔧 Onboarding Wizard", "Onboarding Wizard")

    # Empty-DB detection banner — guides new users to the wizard
    try:
        from src.infrastructure.database import get_engine
        from sqlalchemy import text
        with get_engine().connect() as conn:
            product_count = conn.execute(
                text("SELECT COUNT(*) FROM product_master")
            ).scalar() or 0
        if product_count == 0:
            st.sidebar.warning(
                "👋 **New here?**\n\n"
                "Your catalog is empty. Start with the **Onboarding Wizard** "
                "to import data from your marketplaces."
            )
    except Exception as e:
        # Don't break navigation if DB check fails
        logger.debug(f"Empty-DB check skipped: {e}")

    st.sidebar.divider()
    if st.sidebar.button("🔄 Refresh Data"):
        from src.ui.cache_adapter import clear_all_caches
        clear_all_caches()
        st.rerun()

    try:
        if page == "Home":
            logger.debug("Loading Home Dashboard...")
            from src.ui.pages import home_dashboard
            home_dashboard.render()
        elif page == "Onboarding Wizard":
            logger.debug("Loading Onboarding Wizard...")
            from src.ui.pages import onboarding_wizard
            onboarding_wizard.render()
        elif page == "Profit Dashboard":
            logger.debug("Loading Profit Dashboard...")
            from src.ui.pages import profit_dashboard
            profit_dashboard.render()
        elif page == "Gap Analysis":
            logger.debug("Loading Gap Analysis...")
            from src.ui.pages import gap_dashboard
            gap_dashboard.render()
        elif page == "Demand Planner":
            logger.debug("Loading Demand Planner...")
            from src.ui.pages import demand_planner
            demand_planner.render()
        elif page == "Catalog":
            logger.debug("Loading Catalog...")
            from src.ui.pages import data_manager
            data_manager.render()
        elif page == "Supplier Master":
            logger.debug("Loading Supplier Master...")
            from src.ui.pages import supplier_manager
            supplier_manager.render()
        elif page == "Inventory Manager":
            logger.debug("Loading Inventory Manager...")
            from src.ui.pages import inventory_manager
            inventory_manager.render()
    except Exception as e:
        show_error(logger, f"Error loading the '{page}' page", e)
        st.info("Please check the logs for more details or try refreshing the page.")

except Exception as e:
    logger.critical(f"Critical error in main application: {str(e)}", exc_info=True)
    st.error("❌ Critical application error. Please contact support or check the application logs.")
    sys.exit(1)