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
            logger.error(f"Database initialization failed: {str(e)}", exc_info=True)
            st.error(f"❌ Critical error: Failed to initialize database.\n\nError: {str(e)}")
            st.stop()
    
    st.set_page_config(page_title="Ecommerce ERP", layout="wide")

    st.sidebar.title("Navigation")
    page = st.sidebar.radio(
        "Go to",
        ["Home", "Profit Dashboard", "Gap Analysis", "Demand Planner",
        "Data Manager", "Inventory Manager"]
    )

    st.sidebar.divider()
    if st.sidebar.button("🔄 Refresh Data"):
        from src.core.cache import clear_all_caches
        clear_all_caches()
        st.rerun()

    try:
        if page == "Home":
            logger.debug("Loading Home Dashboard...")
            from src.ui.pages import home_dashboard
            home_dashboard.render()
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
        elif page == "Data Manager":
            logger.debug("Loading Data Manager...")
            from src.ui.pages import data_manager
            data_manager.render()
        elif page == "Inventory Manager":
            logger.debug("Loading Inventory Manager...")
            from src.ui.pages import inventory_manager
            inventory_manager.render()
    except Exception as e:
        logger.error(f"Error rendering page '{page}': {str(e)}", exc_info=True)
        st.error(f"❌ An error occurred while loading the page.\n\nError: {str(e)}")
        st.info("Please check the logs for more details or try refreshing the page.")

except Exception as e:
    logger.critical(f"Critical error in main application: {str(e)}", exc_info=True)
    st.error("❌ Critical application error. Please contact support or check the application logs.")
    sys.exit(1)